import json
import subprocess

ENDPOINTS_PATH = "/home/bbdwz/projects/website/data/cloud/public_endpoints.json"
DISCOVERY_COMMAND = ["sudo", "-n", "/usr/local/libexec/cloud-endpoints", "--stdout"]

def load_public_origins():
    data = {}
    try:
        completed = subprocess.run(DISCOVERY_COMMAND, capture_output=True, text=True, timeout=4, check=True)
        data = json.loads(completed.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass
    if data:
        return data.get("primary", []), data.get("backups", [])
    try:
        with open(ENDPOINTS_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return data.get("primary", []), data.get("backups", [])

def share_links(path, origins=None):
    primary, backups = origins or load_public_origins()
    return ([{"kind": "primary", "label": "FRP 主地址", "url": origin.rstrip("/") + path} for origin in primary] +
            [{"kind": "backup", "label": f"cpolar 备用地址 {index + 1}", "url": origin.rstrip("/") + path}
             for index, origin in enumerate(backups)])
