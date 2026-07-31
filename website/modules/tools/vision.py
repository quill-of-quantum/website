"""Local image counting for the vision tool.

The public API intentionally stays compatible with the original module.  Detection
code is kept here (rather than in app.py) so the tools module remains self-contained.
"""
from __future__ import annotations

import base64
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from flask import Blueprint, current_app, jsonify, request
from ultralytics import YOLO


bp = Blueprint("tool_1", __name__)
MODULE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = MODULE_DIR.parent.parent
UPLOAD_FOLDER = PROJECT_DIR / "storage" / "vision" / "uploads"
MODEL_PATH = MODULE_DIR / "models" / "yolo11s.pt"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
AI_TARGETS = {
    "people": ({0}, "Person", (46, 204, 113)),
    # “车辆”按道路交通工具统计，而不只是 COCO 的 car 类。
    "cars": ({2, 3, 5, 7}, "Vehicle", (255, 142, 43)),
}

_model = None
_model_lock = threading.Lock()
_cache_lock = threading.Lock()
# Cache is only an accelerator.  A worker restart can reconstruct an entry from disk.
IMAGE_CACHE: dict[str, dict] = {}


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = YOLO(str(MODEL_PATH))
    return _model


def _encode_image(image: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise ValueError("无法编码结果图片")
    return base64.b64encode(buffer).decode("ascii")


def _read_image(path: Path) -> np.ndarray | None:
    """imdecode handles non-ASCII paths and applies EXIF orientation when supported."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def _cleanup_old_files(max_size_mb: int = 100) -> None:
    limit = max_size_mb * 1024 * 1024
    files = sorted(
        (p for p in UPLOAD_FOLDER.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    total = sum(p.stat().st_size for p in files)
    for path in files:
        if total <= limit:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
            with _cache_lock:
                IMAGE_CACHE.pop(path.stem, None)
        except OSError:
            continue


def _nms(boxes: list[list[float]], scores: list[float], threshold: float = 0.35) -> list[int]:
    if not boxes:
        return []
    xywh = [[x1, y1, x2 - x1, y2 - y1] for x1, y1, x2, y2 in boxes]
    indices = cv2.dnn.NMSBoxes(xywh, scores, score_threshold=0.01, nms_threshold=threshold)
    return np.asarray(indices).reshape(-1).astype(int).tolist() if len(indices) else []


def _tile_windows(width: int, height: int, tile: int = 960, overlap: float = 0.2):
    """Yield full-coverage tiles; identical edge windows are removed."""
    step = max(1, int(tile * (1 - overlap)))
    xs = list(range(0, max(1, width - tile + 1), step))
    ys = list(range(0, max(1, height - tile + 1), step))
    xs.append(max(0, width - tile))
    ys.append(max(0, height - tile))
    for y in dict.fromkeys(ys):
        for x in dict.fromkeys(xs):
            yield x, y, min(width, x + tile), min(height, y + tile)


def _detect_objects(image: np.ndarray, target: str):
    class_ids, label, color = AI_TARGETS[target]
    h, w = image.shape[:2]
    boxes: list[list[float]] = []
    scores: list[float] = []

    # Start globally. Tiles are enabled only when the scene is actually dense/small;
    # close group photos gain nothing from tiling and are more prone to duplicates.
    jobs = [(0, 0, w, h, image)]

    model = _get_model()
    with _model_lock:
        def run_job(job, image_size=960):
            result = model(job[4], imgsz=image_size, conf=0.08, iou=0.55,
                           max_det=1000, verbose=False)[0]
            ox, oy = job[0], job[1]
            added = []
            for box in result.boxes:
                if int(box.cls[0]) not in class_ids:
                    continue
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                mapped = [x1 + ox, y1 + oy, x2 + ox, y2 + oy]
                boxes.append(mapped)
                scores.append(float(box.conf[0]))
                added.append(mapped)
            return added

        global_boxes = run_job(jobs[0], image_size=1280)
        relative_sizes = [min((b[2]-b[0]) / w, (b[3]-b[1]) / h) for b in global_boxes]
        needs_tiles = (
            max(h, w) > 1500 and
            (not relative_sizes or len(relative_sizes) >= 18 or np.median(relative_sizes) < .09)
        )
        if needs_tiles:
            jobs.extend((x1, y1, x2, y2, image[y1:y2, x1:x2])
                        for x1, y1, x2, y2 in _tile_windows(w, h))
            # Sequential inference caps peak RAM on Raspberry Pi.
            for job in jobs[1:]:
                run_job(job)

    # Close portraits otherwise produce low-confidence boxes around hands/reflections;
    # dense scenes need the lower threshold to retain distant targets.
    confidence = .22 if (not needs_tiles and relative_sizes and np.median(relative_sizes) >= .12) else .10
    filtered = [i for i, score in enumerate(scores) if score >= confidence]
    boxes = [boxes[i] for i in filtered]
    scores = [scores[i] for i in filtered]
    keep = _nms(boxes, scores, threshold=.35 if needs_tiles else .52)
    output = image.copy()
    line = max(2, round(max(h, w) / 900))
    for number, index in enumerate(keep, 1):
        x1, y1, x2, y2 = map(int, boxes[index])
        cv2.rectangle(output, (x1, y1), (x2, y2), color, line)
        cv2.putText(output, f"{number} {scores[index]:.2f}", (x1, max(18, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, line)
    return output, len(keep), {
        "method": "YOLO11s global + tiled inference" if len(jobs) > 1 else "YOLO11s inference",
        "label": label,
        "confidence_threshold": confidence,
        "tiles": len(jobs) - 1,
    }


def _deduplicate_circles(circles: list[tuple[float, float, float, float]]):
    circles.sort(key=lambda item: item[3], reverse=True)
    kept = []
    for candidate in circles:
        x, y, r, _ = candidate
        # The same rim is commonly returned with several nearby radii/passes. Real
        # packed ends have centres about two radii apart, so this remains conservative.
        if any((x-kx) ** 2 + (y-ky) ** 2 < (0.65 * (r + kr)) ** 2
               for kx, ky, kr, _ in kept):
            continue
        kept.append(candidate)
    return kept


def _largest_circle_cluster(circles):
    """Packed stick ends form one dense component; scattered wood-grain rings do not."""
    if len(circles) < 3:
        return []
    parent = list(range(len(circles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    for i, (x, y, r, _) in enumerate(circles):
        for j in range(i):
            xx, yy, rr, _ = circles[j]
            if (x-xx) ** 2 + (y-yy) ** 2 <= (1.45 * (r + rr)) ** 2:
                union(i, j)
    groups = {}
    for i, circle in enumerate(circles):
        groups.setdefault(find(i), []).append(circle)
    cluster = max(groups.values(), key=len)
    median_r = float(np.median([c[2] for c in cluster]))
    cluster = [c for c in cluster if .52 * median_r <= c[2] <= 1.75 * median_r]
    # Avoid confidently reporting texture in an unrelated photograph as bamboo.
    return cluster if len(cluster) >= 5 else []


def _detect_bamboo_ends(image: np.ndarray):
    """Count visible round stick ends using scale-adaptive Hough candidates.

    Gradient circles are substantially more stable than fixed contour area limits on
    tightly packed ends.  A LAB colour/texture score removes circles on plain background.
    """
    h0, w0 = image.shape[:2]
    scale = min(1.0, 1400.0 / max(h0, w0))
    work = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image.copy()
    h, w = work.shape[:2]
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    smooth = cv2.GaussianBlur(enhanced, (0, 0), 1.2)

    # The useful end diameter is normally 0.6–8% of the short image side.
    min_r = max(3, round(min(h, w) * 0.003))
    max_r = max(min_r + 2, round(min(h, w) * 0.045))
    candidates = []
    # Two conservative passes recover weak rims without the combinatorial explosion
    # caused by very low Hough thresholds on wood grain.
    for sensitivity in (30, 34):
        found = cv2.HoughCircles(smooth, cv2.HOUGH_GRADIENT, dp=1.15,
                                 minDist=max(5, min_r * 1.7), param1=100,
                                 param2=sensitivity, minRadius=min_r, maxRadius=max_r)
        if found is None:
            continue
        for x, y, r in found[0]:
            mask = np.zeros((h, w), np.uint8)
            cv2.circle(mask, (round(x), round(y)), max(2, round(r * .72)), 255, -1)
            pixels_l = lab[:, :, 0][mask > 0]
            pixels_b = lab[:, :, 2][mask > 0]
            # Bamboo ends are warm and textured; allow darker burnt ends as long as textured.
            texture = float(np.std(pixels_l))
            warmth = float(np.mean(pixels_b))
            if texture < 3.2 or warmth < 126:
                continue
            score = texture + max(0.0, warmth - 128) * .15 + (28 - sensitivity)
            candidates.append((float(x), float(y), float(r), score))

    circles = _largest_circle_cluster(_deduplicate_circles(candidates))
    output = work.copy()
    for number, (x, y, r, _) in enumerate(sorted(circles, key=lambda c: (c[1], c[0])), 1):
        center = (round(x), round(y))
        cv2.circle(output, center, round(r), (45, 220, 80), 2)
        cv2.circle(output, center, 2, (30, 30, 230), -1)
        if len(circles) <= 100:
            cv2.putText(output, str(number), (center[0] + 3, center[1] - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, .35, (30, 30, 230), 1)

    debug = {
        "1_增强灰度图": _encode_image(enhanced),
        "2_圆形候选结果": _encode_image(output),
    }
    return output, len(circles), {
        "method": "adaptive multi-pass Hough circle detection",
        "radius_range": [min_r, max_r],
        "processing_scale": round(scale, 3),
    }, debug


@bp.route("/api/vision/upload", methods=["POST"])
def upload_image():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "请选择图片文件"}), 400
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "仅支持 JPG、PNG、WebP 或 BMP 图片"}), 400

    payload = file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "图片不能超过 25 MB"}), 413
    image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return jsonify({"error": "文件不是有效图片"}), 400

    _cleanup_old_files()
    image_id = str(uuid.uuid4())
    path = UPLOAD_FOLDER / f"{image_id}.jpg"
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        return jsonify({"error": "图片保存失败"}), 500
    encoded.tofile(path)
    with _cache_lock:
        IMAGE_CACHE[image_id] = {"path": str(path), "created_at": time.time()}
    return jsonify({"image_id": image_id})


@bp.route("/api/vision/analyze", methods=["POST"])
def analyze_image():
    data = request.get_json(silent=True) or {}
    image_id = str(data.get("image_id", ""))
    target = data.get("target")
    if target not in {*AI_TARGETS, "circles"}:
        return jsonify({"error": "未知的检测目标"}), 400
    try:
        uuid.UUID(image_id)
    except ValueError:
        return jsonify({"error": "图片编号无效"}), 400

    with _cache_lock:
        entry = IMAGE_CACHE.get(image_id)
    path = Path(entry["path"]) if entry else UPLOAD_FOLDER / f"{image_id}.jpg"
    if not path.is_file():
        return jsonify({"error": "图片不存在或已过期"}), 404
    image = _read_image(path)
    if image is None:
        return jsonify({"error": "无法读取图片"}), 400

    roi = data.get("roi")
    roi_applied = False
    if isinstance(roi, dict):
        try:
            x = float(roi.get("x", 0))
            y = float(roi.get("y", 0))
            width = float(roi.get("width", 1))
            height = float(roi.get("height", 1))
            if not all(np.isfinite(v) for v in (x, y, width, height)):
                raise ValueError
            x, y = np.clip([x, y], 0.0, 1.0)
            width = min(max(width, 0.0), 1.0 - x)
            height = min(max(height, 0.0), 1.0 - y)
            if width >= .03 and height >= .03:
                ih, iw = image.shape[:2]
                x1, y1 = round(x * iw), round(y * ih)
                x2, y2 = round((x + width) * iw), round((y + height) * ih)
                image = image[y1:y2, x1:x2].copy()
                roi_applied = True
        except (TypeError, ValueError):
            return jsonify({"error": "统计区域参数无效"}), 400

    try:
        if target in AI_TARGETS:
            output, count, details = _detect_objects(image, target)
            debug = {}
        else:
            output, count, details, debug = _detect_bamboo_ends(image)
        details["roi_applied"] = roi_applied
        return jsonify({
            "count": count,
            "image_base64": f"data:image/jpeg;base64,{_encode_image(output)}",
            "debug_images": debug,
            "details": details,
        })
    except Exception as exc:
        current_app.logger.exception("Vision analysis failed")
        return jsonify({"error": f"分析失败：{exc}"}), 500
