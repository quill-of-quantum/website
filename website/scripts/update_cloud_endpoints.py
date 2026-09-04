#!/usr/bin/env python3
import json
import os
import re
import tomllib
import urllib.request
import sys

FRP_CONFIG = "/opt/frp/frpc.toml"
CPOLAR_CONFIG = "/usr/local/etc/cpolar/cpolar.yml"
OUTPUT = "/home/bbdwz/projects/website/data/cloud/public_endpoints.json"

REGION_SUFFIXES = {
    "eu": "eu.cpolar.io", "us": "us.cpolar.io", "hk": "hk.cpolar.io",
    "tw": "tw.cpolar.io", "ap": "ap.cpolar.io", "au": "au.cpolar.io",
    "cn_vip": "vip.cpolar.cn", "cn_vip_top": "vip.cpolar.top",
    "cn_top": "top.cpolar.cn", "cn": "cpolar.top",
}

def frp_origins():
    with open(FRP_CONFIG, "rb") as file:
        config = tomllib.load(file)
    host = config.get("serverAddr")
    origins = []
    for proxy in config.get("proxies", []):
        if proxy.get("type") == "tcp" and int(proxy.get("localPort", 0)) == 80 and host and proxy.get("remotePort"):
            origins.append(f"http://{host}:{int(proxy['remotePort'])}")
    return origins

def cpolar_config_origins():
    text = open(CPOLAR_CONFIG, encoding="utf-8").read()
    origins, current = [], None
    for line in text.splitlines():
        tunnel = re.match(r"^  ([\w.-]+):\s*$", line)
        if tunnel:
            if current: origins.extend(cpolar_tunnel_origins(current))
            current = {"name": tunnel.group(1)}
            continue
        setting = re.match(r"^    ([\w_]+):\s*[\"']?([^\"'#]+)", line)
        if current and setting:
            current[setting.group(1)] = setting.group(2).strip()
    if current: origins.extend(cpolar_tunnel_origins(current))
    return origins

def cpolar_tunnel_origins(tunnel):
    if tunnel.get("proto") != "http" or str(tunnel.get("addr", "")).strip() != "80": return []
    if tunnel.get("hostname"): return [f"https://{tunnel['hostname']}"]
    suffix = REGION_SUFFIXES.get(tunnel.get("region", ""))
    if tunnel.get("subdomain") and suffix: return [f"https://{tunnel['subdomain']}.{suffix}"]
    return []

def cpolar_runtime_origins():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:4040/api/tunnels", timeout=2) as response:
            data = json.load(response)
        tunnels = data.get("tunnels", data) if isinstance(data, dict) else data
        return [item.get("public_url") or item.get("PublicUrl") for item in tunnels or []
                if str(item.get("config", {}).get("addr", item.get("addr", ""))).endswith("80")]
    except Exception:
        return []

def main():
    primary, backups = [], []
    try: primary = frp_origins()
    except Exception: pass
    try: backups.extend(cpolar_config_origins())
    except Exception: pass
    backups.extend(cpolar_runtime_origins())
    primary = list(dict.fromkeys(filter(None, primary)))
    backups = [url.rstrip("/") for url in dict.fromkeys(filter(None, backups)) if url.rstrip("/") not in primary]
    result = {"primary": primary, "backups": backups}
    if "--stdout" in sys.argv:
        print(json.dumps(result, ensure_ascii=False))
        return
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    temporary = OUTPUT + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    os.chmod(temporary, 0o644)
    os.replace(temporary, OUTPUT)

if __name__ == "__main__": main()
