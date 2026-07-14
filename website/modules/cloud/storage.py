import json
import os


UPLOAD_FOLDER = "/home/bbdwz/projects/website/storage/cloud/uploads"
THUMBNAIL_FOLDER = "/home/bbdwz/projects/website/storage/cloud/thumbnails"
UPLOAD_META_PATH = os.path.join(UPLOAD_FOLDER, ".meta.json")


def ensure_storage_dirs():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)


def load_meta():
    if not os.path.exists(UPLOAD_META_PATH):
        return {}
    try:
        with open(UPLOAD_META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_meta(meta):
    ensure_storage_dirs()
    tmp_path = f"{UPLOAD_META_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp_path, UPLOAD_META_PATH)


def resolve_stored_name(name, meta):
    candidate = os.path.join(UPLOAD_FOLDER, name)
    if os.path.exists(candidate):
        return name

    matches = [
        stored for stored, info in meta.items()
        if info.get("original_name") == name
    ]
    if len(matches) == 1:
        return matches[0]
    return None


ensure_storage_dirs()
