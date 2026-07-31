import io
from pathlib import Path

import cv2
import numpy as np
from flask import Flask

from modules.tools.vision import _nms, bp


def _app():
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app


def test_nms_merges_overlapping_tile_detections():
    boxes = [[10, 10, 50, 60], [12, 11, 51, 61], [100, 100, 130, 140]]
    assert _nms(boxes, [.9, .8, .7]) == [0, 2]


def test_upload_rejects_non_image():
    client = _app().test_client()
    response = client.post("/api/vision/upload", data={
        "file": (io.BytesIO(b"not an image"), "bad.jpg")
    })
    assert response.status_code == 400


def test_upload_and_analyze_validation():
    client = _app().test_client()
    image = np.full((80, 100, 3), 255, np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    upload = client.post("/api/vision/upload", data={
        "file": (io.BytesIO(encoded.tobytes()), "test.jpg")
    })
    assert upload.status_code == 200
    image_id = upload.get_json()["image_id"]
    try:
        response = client.post("/api/vision/analyze", json={
            "image_id": image_id, "target": "unknown"
        })
        assert response.status_code == 400
        bad_roi = client.post("/api/vision/analyze", json={
            "image_id": image_id, "target": "people",
            "roi": {"x": "bad", "y": 0, "width": 1, "height": 1},
        })
        assert bad_roi.status_code == 400
    finally:
        (Path(__file__).parents[2] / "storage" / "vision" / "uploads" /
         f"{image_id}.jpg").unlink(missing_ok=True)
