from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, Response
from functools import wraps
import os
import time
import json
import ipaddress
import psutil
import socket
import subprocess
import shutil
import threading
import re
from collections import deque
import geoip2.database
import geoip2.errors

bp = Blueprint("admin", __name__)

ADMIN_PREFIX = "/1"
VISITER_LOG_PATH = "/home/bbdwz/projects/website/logs/visiter.log"
GEOIP_DB_PATH = "/home/bbdwz/projects/website/GeoLite2-City.mmdb"
NAS_STATE_PATH = "/home/bbdwz/projects/website/nas_state.json"
NAS_MANAGED_CONF_PATH = "/etc/samba/smb.conf.d/website-nas.conf"
NAS_SMB_CONF_PATH = "/etc/samba/smb.conf"

def _get_lan_networks():
    nets = []
    try:
        for _, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                if not addr.address or addr.address.startswith("127."):
                    continue
                if not addr.netmask:
                    continue
                try:
                    net = ipaddress.ip_network(f"{addr.address}/{addr.netmask}", strict=False)
                except Exception:
                    continue
                nets.append(net)
    except Exception:
        pass
    if not nets:
        nets.append(ipaddress.ip_network("192.168.178.0/24"))
    return nets

LAN_NETWORKS = _get_lan_networks()
GEOIP_LOCK = threading.Lock()
GEOIP_READER = None
GEOIP_CACHE = {}
GEOIP_CACHE_MAX = 2000

CLIENT_TIMEOUT_SEC = 120
CLIENT_PATH_TIMEOUT_SEC = 120
CLIENTS_LOCK = threading.Lock()
CLIENTS = {}
REQUESTS_LOCK = threading.Lock()
REQUEST_TIMES = deque()
REQUEST_TIMES_BY_NET = deque()
REQUEST_WINDOW_SEC = 24 * 3600
NET_METRICS_LOCK = threading.Lock()
NET_METRICS = deque()
NET_METRICS_WINDOW_SEC = 24 * 3600
NET_METRICS_MAX_MS = 60000
SAMPLE_MAX_POINTS = None
AGG_WINDOW_HOURS = 14 * 24
REQUEST_HOURLY = deque()
NET_HOURLY = deque()
LATENCY_BUCKETS_MS = [
    (0, 50),
    (50, 100),
    (100, 200),
    (200, 500),
    (500, 1000),
    (1000, 2000),
    (2000, 5000),
    (5000, None),
]

def is_lan_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in LAN_NETWORKS)
    except Exception:
        return False

def _get_geo_reader():
    global GEOIP_READER
    with GEOIP_LOCK:
        if GEOIP_READER is not None:
            return GEOIP_READER
        if not os.path.exists(GEOIP_DB_PATH):
            return None
        try:
            GEOIP_READER = geoip2.database.Reader(GEOIP_DB_PATH)
        except Exception:
            GEOIP_READER = None
        return GEOIP_READER

def _cache_geo(ip, value):
    if ip in GEOIP_CACHE:
        GEOIP_CACHE[ip] = value
        return
    if len(GEOIP_CACHE) >= GEOIP_CACHE_MAX:
        GEOIP_CACHE.pop(next(iter(GEOIP_CACHE)))
    GEOIP_CACHE[ip] = value

def _normalize_ip(ip):
    if not ip:
        return ""
    if "," in ip:
        return ip.split(",", 1)[0].strip()
    return ip.strip()

def _lookup_geo(ip):
    ip = _normalize_ip(ip)
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private:
            return "内网"
    except Exception:
        return ""
    if ip in GEOIP_CACHE:
        return GEOIP_CACHE[ip]
    reader = _get_geo_reader()
    if not reader:
        return ""
    try:
        resp = reader.city(ip)
        country = resp.country.names.get("en") if resp.country else ""
        city = resp.city.names.get("en") if resp.city else ""
        if city and country:
            value = f"{city}, {country}"
        elif country:
            value = country
        elif city:
            value = city
        else:
            value = ""
    except geoip2.errors.AddressNotFoundError:
        value = ""
    except Exception:
        value = ""
    _cache_geo(ip, value)
    return value

def record_visit(ip, path):
    now = time.time()
    with CLIENTS_LOCK:
        entry = CLIENTS.setdefault(ip, {"last_seen": now, "paths": {}})
        entry["last_seen"] = now
        entry["paths"][path] = now
        _prune_clients_locked(now)

def record_request_timing(duration_ms, is_lan=None):
    now = time.time()
    with REQUESTS_LOCK:
        REQUEST_TIMES.append((now, duration_ms))
        if is_lan is not None:
            REQUEST_TIMES_BY_NET.append((now, is_lan, duration_ms))
        _prune_requests_locked(now)
    _record_request_hourly(now, duration_ms)

def _prune_requests_locked(now):
    cutoff = now - REQUEST_WINDOW_SEC
    while REQUEST_TIMES and REQUEST_TIMES[0][0] < cutoff:
        REQUEST_TIMES.popleft()
    while REQUEST_TIMES_BY_NET and REQUEST_TIMES_BY_NET[0][0] < cutoff:
        REQUEST_TIMES_BY_NET.popleft()

def _record_request_hourly(ts, duration_ms):
    hour = int(ts // 3600)
    if not REQUEST_HOURLY or REQUEST_HOURLY[-1]["hour"] != hour:
        REQUEST_HOURLY.append({
            "hour": hour,
            "counts": [0 for _ in LATENCY_BUCKETS_MS],
            "sum": 0.0,
            "count": 0
        })
    entry = REQUEST_HOURLY[-1]
    entry["sum"] += duration_ms
    entry["count"] += 1
    for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
        if high is None:
            if duration_ms >= low:
                entry["counts"][idx] += 1
            continue
        if low <= duration_ms < high:
            entry["counts"][idx] += 1
            break
    _prune_request_hourly(int(ts // 3600))

def _prune_request_hourly(current_hour):
    cutoff = current_hour - AGG_WINDOW_HOURS + 1
    while REQUEST_HOURLY and REQUEST_HOURLY[0]["hour"] < cutoff:
        REQUEST_HOURLY.popleft()

def _get_latency_stats():
    now = time.time()
    with REQUESTS_LOCK:
        _prune_requests_locked(now)
        samples = list(REQUEST_TIMES)
    total = len(samples)
    counts = [0 for _ in LATENCY_BUCKETS_MS]
    for _, dur in samples:
        for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
            if high is None:
                if dur >= low:
                    counts[idx] += 1
                continue
            if low <= dur < high:
                counts[idx] += 1
                break
    buckets = []
    for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
        label = f"{low}-{high}ms" if high is not None else f">={low}ms"
        buckets.append({
            "label": label,
            "count": counts[idx],
        })
    avg = 0
    if total:
        avg = sum(d for _, d in samples) / total
    return {
        "window_sec": REQUEST_WINDOW_SEC,
        "total": total,
        "avg_ms": round(avg, 2),
        "buckets": buckets
    }

def _get_latency_agg_14d():
    counts = [0 for _ in LATENCY_BUCKETS_MS]
    total_count = 0
    total_sum = 0.0
    for entry in REQUEST_HOURLY:
        total_count += entry["count"]
        total_sum += entry["sum"]
        for idx, val in enumerate(entry["counts"]):
            counts[idx] += val
    buckets = []
    for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
        label = f"{low}-{high}ms" if high is not None else f">={low}ms"
        buckets.append({
            "label": label,
            "count": counts[idx],
        })
    avg = total_sum / total_count if total_count else 0
    return {
        "window_hours": AGG_WINDOW_HOURS,
        "total": total_count,
        "avg_ms": round(avg, 2),
        "buckets": buckets
    }

def _get_request_samples(limit=SAMPLE_MAX_POINTS):
    now = time.time()
    with REQUESTS_LOCK:
        _prune_requests_locked(now)
        samples = list(REQUEST_TIMES)
    if limit:
        samples = samples[-limit:]
    return [round(dur, 2) for _, dur in samples]

def _get_request_samples_by_net(limit=SAMPLE_MAX_POINTS):
    now = time.time()
    with REQUESTS_LOCK:
        _prune_requests_locked(now)
        samples = list(REQUEST_TIMES_BY_NET)
    if limit:
        samples = samples[-limit:]
    lan = [round(dur, 2) for _, is_lan, dur in samples if is_lan]
    wan = [round(dur, 2) for _, is_lan, dur in samples if not is_lan]
    return {"lan": lan, "wan": wan}

def _sanitize_ms(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 0:
        return None
    return min(num, NET_METRICS_MAX_MS)

def record_network_metrics(data, client_ip):
    now = time.time()
    metrics = {
        "dns_ms": _sanitize_ms(data.get("dns_ms")),
        "connect_ms": _sanitize_ms(data.get("connect_ms")),
        "ttfb_ms": _sanitize_ms(data.get("ttfb_ms")),
        "download_ms": _sanitize_ms(data.get("download_ms")),
        "total_ms": _sanitize_ms(data.get("total_ms")),
    }
    is_lan = is_lan_ip(client_ip)
    with NET_METRICS_LOCK:
        NET_METRICS.append((now, is_lan, metrics))
        _prune_network_metrics_locked(now)
    _record_network_hourly(now, is_lan, metrics)

def _prune_network_metrics_locked(now):
    cutoff = now - NET_METRICS_WINDOW_SEC
    while NET_METRICS and NET_METRICS[0][0] < cutoff:
        NET_METRICS.popleft()

def _init_segment_bucket():
    return {
        "counts": [0 for _ in LATENCY_BUCKETS_MS],
        "sum": 0.0,
        "count": 0
    }

def _record_network_hourly(ts, is_lan, metrics):
    hour = int(ts // 3600)
    if not NET_HOURLY or NET_HOURLY[-1]["hour"] != hour:
        NET_HOURLY.append({
            "hour": hour,
            "segments": {
                "dns_ms": {"lan": _init_segment_bucket(), "wan": _init_segment_bucket()},
                "connect_ms": {"lan": _init_segment_bucket(), "wan": _init_segment_bucket()},
                "ttfb_ms": {"lan": _init_segment_bucket(), "wan": _init_segment_bucket()},
                "download_ms": {"lan": _init_segment_bucket(), "wan": _init_segment_bucket()},
                "total_ms": {"lan": _init_segment_bucket(), "wan": _init_segment_bucket()},
            }
        })
    entry = NET_HOURLY[-1]
    key = "lan" if is_lan else "wan"
    for seg, val in metrics.items():
        if val is None:
            continue
        bucket = entry["segments"][seg][key]
        bucket["sum"] += val
        bucket["count"] += 1
        for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
            if high is None:
                if val >= low:
                    bucket["counts"][idx] += 1
                continue
            if low <= val < high:
                bucket["counts"][idx] += 1
                break
    _prune_network_hourly(int(ts // 3600))

def _prune_network_hourly(current_hour):
    cutoff = current_hour - AGG_WINDOW_HOURS + 1
    while NET_HOURLY and NET_HOURLY[0]["hour"] < cutoff:
        NET_HOURLY.popleft()

def _segment_stats(samples, key):
    counts = [0 for _ in LATENCY_BUCKETS_MS]
    values = []
    for _, _, metrics in samples:
        val = metrics.get(key)
        if val is None:
            continue
        values.append(val)
        for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
            if high is None:
                if val >= low:
                    counts[idx] += 1
                continue
            if low <= val < high:
                counts[idx] += 1
                break
    avg = sum(values) / len(values) if values else 0
    buckets = []
    for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
        label = f"{low}-{high}ms" if high is not None else f">={low}ms"
        buckets.append({
            "label": label,
            "count": counts[idx],
        })
    return {"avg_ms": round(avg, 2), "buckets": buckets, "samples": len(values)}

def _get_network_latency_stats():
    now = time.time()
    with NET_METRICS_LOCK:
        _prune_network_metrics_locked(now)
        samples = list(NET_METRICS)

    lan_samples = [s for s in samples if s[1]]
    wan_samples = [s for s in samples if not s[1]]
    segments = ["dns_ms", "connect_ms", "ttfb_ms", "download_ms", "total_ms"]
    data = {}
    for seg in segments:
        data[seg] = {
            "lan": _segment_stats(lan_samples, seg),
            "wan": _segment_stats(wan_samples, seg),
        }
    return {
        "window_sec": NET_METRICS_WINDOW_SEC,
        "segments": data
    }

def _get_network_latency_agg_14d():
    segments = ["dns_ms", "connect_ms", "ttfb_ms", "download_ms", "total_ms"]
    out = {seg: {"lan": _init_segment_bucket(), "wan": _init_segment_bucket()} for seg in segments}
    for entry in NET_HOURLY:
        for seg in segments:
            for key in ("lan", "wan"):
                target = out[seg][key]
                source = entry["segments"][seg][key]
                target["sum"] += source["sum"]
                target["count"] += source["count"]
                for idx, val in enumerate(source["counts"]):
                    target["counts"][idx] += val
    result = {}
    for seg in segments:
        result[seg] = {}
        for key in ("lan", "wan"):
            bucket = out[seg][key]
            avg = bucket["sum"] / bucket["count"] if bucket["count"] else 0
            buckets = []
            for idx, (low, high) in enumerate(LATENCY_BUCKETS_MS):
                label = f"{low}-{high}ms" if high is not None else f">={low}ms"
                buckets.append({"label": label, "count": bucket["counts"][idx]})
            result[seg][key] = {
                "avg_ms": round(avg, 2),
                "buckets": buckets,
                "samples": bucket["count"]
            }
    return {
        "window_hours": AGG_WINDOW_HOURS,
        "segments": result
    }

def _get_network_latency_samples(limit=SAMPLE_MAX_POINTS):
    now = time.time()
    with NET_METRICS_LOCK:
        _prune_network_metrics_locked(now)
        samples = list(NET_METRICS)
    if limit:
        samples = samples[-limit:]
    segments = ["dns_ms", "connect_ms", "ttfb_ms", "download_ms", "total_ms"]
    out = {seg: {"lan": [], "wan": []} for seg in segments}
    for _, is_lan, metrics in samples:
        for seg in segments:
            val = metrics.get(seg)
            if val is None:
                continue
            out[seg]["lan" if is_lan else "wan"].append(round(val, 2))
    return out

def _prune_clients_locked(now):
    expired_ips = [
        ip for ip, data in CLIENTS.items()
        if now - data.get("last_seen", 0) > CLIENT_TIMEOUT_SEC
    ]
    for ip in expired_ips:
        CLIENTS.pop(ip, None)
        continue
    for data in CLIENTS.values():
        paths = data.get("paths", {})
        expired_paths = [
            p for p, ts in paths.items()
            if now - ts > CLIENT_PATH_TIMEOUT_SEC
        ]
        for p in expired_paths:
            paths.pop(p, None)

def _get_clients_snapshot():
    now = time.time()
    with CLIENTS_LOCK:
        _prune_clients_locked(now)
        clients = []
        for ip, data in CLIENTS.items():
            paths = data.get("paths", {})
            sorted_paths = sorted(paths.items(), key=lambda x: x[1], reverse=True)
            clients.append({
                "ip": ip,
                "last_seen": data.get("last_seen", now),
                "paths": [p for p, _ in sorted_paths]
            })
        clients.sort(key=lambda x: x["last_seen"], reverse=True)
    return {
        "count": len(clients),
        "clients": clients
    }

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith(ADMIN_PREFIX + "/api") or request.path.startswith("/api/"):
                return jsonify({"require_login": True}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

def _default_nas_state():
    return {
        "version": 1,
        "main_admin": "",
        "users": {},
        "shares": {},
        "mount": {
            "device": "",
            "mount_point": "",
            "fs_type": "ext4"
        },
        "sleep": {
            "device": "",
            "minutes": 0
        }
    }

def _load_nas_state():
    data = _default_nas_state()
    try:
        if os.path.exists(NAS_STATE_PATH):
            with open(NAS_STATE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    data[key] = value
    except Exception:
        pass
    if "users" not in data or not isinstance(data["users"], dict):
        data["users"] = {}
    if "shares" not in data or not isinstance(data["shares"], dict):
        data["shares"] = {}
    if "mount" not in data or not isinstance(data["mount"], dict):
        data["mount"] = {"device": "", "mount_point": "", "fs_type": "ext4"}
    if "sleep" not in data or not isinstance(data["sleep"], dict):
        data["sleep"] = {"device": "", "minutes": 0}
    return data

def _save_nas_state(state):
    tmp_path = NAS_STATE_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True, indent=2)
        os.replace(tmp_path, NAS_STATE_PATH)
        return True, None
    except Exception as e:
        return False, str(e)

def _validate_username(name):
    if not name or not isinstance(name, str):
        return False
    if len(name) > 32:
        return False
    return re.match(r"^[a-zA-Z0-9._-]+$", name) is not None

def _validate_share_name(name):
    if not name or not isinstance(name, str):
        return False
    if len(name) > 32:
        return False
    return re.match(r"^[a-zA-Z0-9._-]+$", name) is not None

def _run_cmd(args):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def _run_cmd_input(args, input_text):
    try:
        result = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def _run_root_cmd(args):
    if os.geteuid() == 0:
        return _run_cmd(args)
    sudo_path = shutil.which("sudo", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not sudo_path:
        return False, "", "sudo-not-found"
    return _run_cmd([sudo_path, "-n"] + args)

def _run_root_cmd_input(args, input_text):
    if os.geteuid() == 0:
        return _run_cmd_input(args, input_text)
    sudo_path = shutil.which("sudo", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not sudo_path:
        return False, "", "sudo-not-found"
    return _run_cmd_input([sudo_path, "-n"] + args, input_text)

def _read_root_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return True, f.read(), None
    except Exception:
        ok, out, err = _run_root_cmd(["cat", path])
        if ok:
            return True, out, None
        return False, "", err

def _write_root_file(path, content):
    if os.geteuid() == 0:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, str(e)
    sudo_path = shutil.which("sudo", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not sudo_path:
        return False, "sudo-not-found"
    try:
        result = subprocess.run(
            [sudo_path, "-n", "tee", path],
            input=content,
            text=True,
            capture_output=True,
            check=False
        )
        if result.returncode != 0:
            return False, (result.stderr or "").strip()
        return True, None
    except Exception as e:
        return False, str(e)

def _ensure_samba_include():
    include_line = f"include = {NAS_MANAGED_CONF_PATH}"
    ok, content, err = _read_root_file(NAS_SMB_CONF_PATH)
    if not ok:
        return False, f"read-smbconf-failed: {err}"
    if include_line.lower() in content.lower():
        return True, "already"
    lines = content.splitlines()
    inserted = False
    for idx, line in enumerate(lines):
        if line.strip().lower() == "[global]":
            lines.insert(idx + 1, include_line)
            inserted = True
            break
    if not inserted:
        lines.append("")
        lines.append("[global]")
        lines.append(include_line)
    new_content = "\n".join(lines).rstrip() + "\n"
    ok, err = _write_root_file(NAS_SMB_CONF_PATH, new_content)
    if not ok:
        return False, f"write-smbconf-failed: {err}"
    return True, "added"

def _sanitize_smb_value(value):
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text

def _normalize_user_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [v for v in (str(x).strip() for x in value) if v]
    return [v for v in (x.strip() for x in str(value).split(",")) if v]

def _render_samba_conf(state):
    lines = [
        "# Managed by website/app.py",
        "# Do not edit manually. Use the NAS panel instead.",
        ""
    ]
    shares = state.get("shares", {}) or {}
    for name, cfg in shares.items():
        if not _validate_share_name(name):
            continue
        path = _sanitize_smb_value(cfg.get("path", ""))
        if not path:
            continue
        lines.append(f"[{name}]")
        lines.append(f"  path = {path}")
        lines.append("  browseable = yes")
        lines.append(f"  read only = {'yes' if cfg.get('read_only') else 'no'}")
        lines.append(f"  guest ok = {'yes' if cfg.get('guest_ok') else 'no'}")
        comment = _sanitize_smb_value(cfg.get("comment", ""))
        if comment:
            lines.append(f"  comment = {comment}")
        valid_users = _normalize_user_list(cfg.get("valid_users"))
        if valid_users:
            lines.append(f"  valid users = {' '.join(valid_users)}")
        admin_users = _normalize_user_list(cfg.get("admin_users"))
        if admin_users:
            lines.append(f"  admin users = {' '.join(admin_users)}")
        write_list = _normalize_user_list(cfg.get("write_list"))
        if write_list:
            lines.append(f"  write list = {' '.join(write_list)}")
        create_mask = _sanitize_smb_value(cfg.get("create_mask", ""))
        if create_mask:
            lines.append(f"  create mask = {create_mask}")
        directory_mask = _sanitize_smb_value(cfg.get("directory_mask", ""))
        if directory_mask:
            lines.append(f"  directory mask = {directory_mask}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

def _write_samba_managed_conf(state):
    content = _render_samba_conf(state)
    ok, err = _write_root_file(NAS_MANAGED_CONF_PATH, content)
    if not ok:
        return False, err
    return True, None

def _get_samba_users():
    ok, out, err = _run_root_cmd(["pdbedit", "-L"])
    if not ok:
        return [], err
    users = []
    for line in out.splitlines():
        if ":" in line:
            name = line.split(":", 1)[0].strip()
            if name:
                users.append(name)
    users.sort()
    return users, None

def _get_lsblk():
    ok, out, err = _run_cmd(["lsblk", "-J", "-o", "NAME,KNAME,TYPE,FSTYPE,SIZE,LABEL,UUID,MOUNTPOINT"])
    if not ok:
        return None, err
    try:
        return json.loads(out), None
    except Exception as e:
        return None, str(e)

def _get_findmnt():
    ok, out, err = _run_cmd(["findmnt", "-J"])
    if not ok:
        return None, err
    try:
        return json.loads(out), None
    except Exception as e:
        return None, str(e)

def _hdparm_value_from_minutes(minutes):
    if minutes <= 0:
        return 0
    if minutes <= 20:
        return int(max(1, min(240, round(minutes * 12))))
    if minutes <= 330:
        return int(241 + round((minutes - 30) / 30))
    return 255

def _get_samba_status():
    return {
        "smbd": _get_service_status("smbd.service"),
        "nmbd": _get_service_status("nmbd.service")
    }

def _get_service_status(service_name):
    systemctl_path = shutil.which("systemctl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not systemctl_path:
        return "no-systemctl"
    try:
        result = subprocess.run(
            [systemctl_path, "show", "-p", "ActiveState", "-p", "SubState", service_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").lower()
            if "not found" in stderr or "not-found" in stderr:
                return "not_found"
            if "system has not been booted" in stderr:
                return "no-systemd"
            return "unknown"
        active = None
        sub = None
        for line in result.stdout.splitlines():
            if line.startswith("ActiveState="):
                active = line.split("=", 1)[1].strip()
            elif line.startswith("SubState="):
                sub = line.split("=", 1)[1].strip()
        if active:
            if sub and sub != active:
                return f"{active} ({sub})"
            return active
        return "unknown"
    except Exception:
        return "unknown"

def _get_wifi_interfaces():
    interfaces = []
    try:
        with open("/proc/net/wireless", "r") as f:
            for line in f.readlines()[2:]:
                name = line.split(":")[0].strip()
                if name:
                    interfaces.append(name)
    except Exception:
        pass
    return interfaces

def _read_wireless_signal(iface):
    try:
        with open("/proc/net/wireless", "r") as f:
            for line in f.readlines()[2:]:
                if line.strip().startswith(iface + ":"):
                    parts = line.split()
                    if len(parts) >= 3:
                        link = parts[2].strip(".")
                        if link.replace(".", "", 1).isdigit():
                            link_val = float(link)
                            percent = int(max(0, min(100, (link_val / 70.0) * 100)))
                            return percent
    except Exception:
        pass
    return None

def _get_wifi_info():
    ssid = None
    signal = None
    try:
        nmcli_path = shutil.which("nmcli", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        iwgetid_path = shutil.which("iwgetid", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        iw_path = shutil.which("iw", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        if nmcli_path:
            result = subprocess.run(
                [nmcli_path, "-t", "-f", "active,ssid,signal", "dev", "wifi"],
                capture_output=True,
                text=True,
                check=False
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split(":", 2)
                if len(parts) >= 2 and parts[0] == "yes":
                    ssid = parts[1] or None
                    if len(parts) == 3 and parts[2].isdigit():
                        signal = int(parts[2])
                    break
        if not ssid and iwgetid_path:
            result = subprocess.run(
                [iwgetid_path, "-r"],
                capture_output=True,
                text=True,
                check=False
            )
            ssid = result.stdout.strip() or None
        if not ssid and iw_path:
            interfaces = _get_wifi_interfaces()
            for iface in interfaces:
                result = subprocess.run(
                    [iw_path, "dev", iface, "link"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                for line in result.stdout.splitlines():
                    if line.strip().startswith("SSID:"):
                        ssid = line.split("SSID:", 1)[1].strip() or None
                        break
                if ssid:
                    break
        if signal is None:
            interfaces = _get_wifi_interfaces()
            if interfaces:
                signal = _read_wireless_signal(interfaces[0])
    except Exception:
        pass
    return {"ssid": ssid, "signal": signal}

def _get_local_ip():
    ip = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = None
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return ip

def _get_status_payload():
    services = [
        "website.service",
        "cpolar.service",
        "gallery.service",
        "tracker_scheduler.service"
    ]
    service_status = {name: _get_service_status(name) for name in services}
    payload = {
        "timestamp": int(time.time()),
        "services": service_status,
        "network": {
            "wifi": _get_wifi_info(),
            "local_ip": _get_local_ip()
        },
        "clients": _get_clients_snapshot(),
        "latency": _get_latency_stats(),
        "network_latency": _get_network_latency_stats(),
        "latency_samples": _get_request_samples(),
        "latency_samples_by_net": _get_request_samples_by_net(),
        "network_latency_samples": _get_network_latency_samples(),
        "latency_agg_14d": _get_latency_agg_14d(),
        "network_latency_agg_14d": _get_network_latency_agg_14d()
    }
    return payload

def _read_log_tail(path, max_lines=1000):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            tail = deque(f, maxlen=max_lines)
        lines = [line.rstrip("\n") for line in tail]
        lines.reverse()
        return lines
    except FileNotFoundError:
        return []
    except Exception as e:
        return [f"读取日志失败: {e}"]

def _parse_log_line(line):
    if line.startswith("时间:"):
        parts = line.split("\t")
        data = {}
        for part in parts:
            idx = part.find(":")
            if idx == -1:
                continue
            key = part[:idx]
            val = part[idx + 1:]
            data[key] = val
        ip = data.get("IP", "")
        return {
            "time": data.get("时间", ""),
            "ip": ip,
            "path": data.get("路径", ""),
            "login": data.get("登录", ""),
            "op": data.get("操作", ""),
            "device": data.get("设备", "")
        }
    parts = line.split("\t")
    ip = parts[1] if len(parts) > 1 else ""
    return {
        "time": parts[0] if len(parts) > 0 else "",
        "ip": ip,
        "path": parts[2] if len(parts) > 2 else "",
        "login": parts[3] if len(parts) > 3 else "",
        "op": parts[4] if len(parts) > 4 else "",
        "device": parts[5] if len(parts) > 5 else "",
    }

def _json_error(message, code=400, detail=None):
    payload = {"ok": False, "error": message}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), code

def _json_ok(data=None):
    payload = {"ok": True}
    if data:
        payload.update(data)
    return jsonify(payload)

@bp.route(ADMIN_PREFIX + "/nas")
@login_required
def admin_nas():
    return render_template("nas.html", user=session.get("user"))

@bp.route(ADMIN_PREFIX + "/api/nas/status")
@login_required
def nas_status():
    state = _load_nas_state()
    include_ok = False
    include_msg = ""
    ok, content, err = _read_root_file(NAS_SMB_CONF_PATH)
    if ok:
        include_ok = f"include = {NAS_MANAGED_CONF_PATH}".lower() in content.lower()
        include_msg = "present" if include_ok else "missing"
    else:
        include_msg = err or "read-failed"
    samba_users, samba_err = _get_samba_users()
    disks, disks_err = _get_lsblk()
    mounts, mounts_err = _get_findmnt()
    return _json_ok({
        "state": state,
        "samba": {
            "service": _get_samba_status(),
            "include_ok": include_ok,
            "include_msg": include_msg,
            "managed_conf_path": NAS_MANAGED_CONF_PATH,
            "samba_users": samba_users,
            "samba_users_err": samba_err
        },
        "disks": disks,
        "disks_err": disks_err,
        "mounts": mounts,
        "mounts_err": mounts_err
    })

@bp.route(ADMIN_PREFIX + "/api/nas/service", methods=["POST"])
@login_required
def nas_service():
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    service = data.get("service", "smbd")
    if action not in ("start", "stop", "restart", "reload"):
        return _json_error("invalid-action")
    unit = "smbd.service" if service == "smbd" else "nmbd.service"
    ok, out, err = _run_root_cmd(["systemctl", action, unit])
    if not ok:
        return _json_error("systemctl-failed", detail=err or out)
    return _json_ok({"status": _get_service_status(unit)})

@bp.route(ADMIN_PREFIX + "/api/nas/include", methods=["POST"])
@login_required
def nas_include():
    ok, msg = _ensure_samba_include()
    if not ok:
        return _json_error("include-failed", detail=msg)
    return _json_ok({"result": msg})

@bp.route(ADMIN_PREFIX + "/api/nas/users/create", methods=["POST"])
@login_required
def nas_user_create():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    is_admin = bool(data.get("is_admin"))
    if not _validate_username(username):
        return _json_error("invalid-username")
    if not password:
        return _json_error("missing-password")
    ok, out, err = _run_root_cmd(["useradd", "-M", "-s", "/usr/sbin/nologin", username])
    if not ok and "already exists" not in (err or "").lower():
        return _json_error("useradd-failed", detail=err or out)
    smb_cmd = ["smbpasswd", "-a", "-s", username]
    ok, out, err = _run_root_cmd_input(smb_cmd, f"{password}\n{password}\n")
    if not ok:
        return _json_error("smbpasswd-failed", detail=err or out)
    state = _load_nas_state()
    state["users"][username] = {"is_admin": is_admin}
    if is_admin and not state.get("main_admin"):
        state["main_admin"] = username
    _save_nas_state(state)
    return _json_ok({"users": state["users"]})

@bp.route(ADMIN_PREFIX + "/api/nas/users/delete", methods=["POST"])
@login_required
def nas_user_delete():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not _validate_username(username):
        return _json_error("invalid-username")
    ok, out, err = _run_root_cmd(["smbpasswd", "-x", username])
    if not ok and "does not exist" not in (err or "").lower():
        return _json_error("smbpasswd-delete-failed", detail=err or out)
    ok, out, err = _run_root_cmd(["userdel", username])
    if not ok and "does not exist" not in (err or "").lower():
        return _json_error("userdel-failed", detail=err or out)
    state = _load_nas_state()
    state["users"].pop(username, None)
    if state.get("main_admin") == username:
        state["main_admin"] = ""
    _save_nas_state(state)
    return _json_ok({"users": state["users"]})

@bp.route(ADMIN_PREFIX + "/api/nas/users/password", methods=["POST"])
@login_required
def nas_user_password():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not _validate_username(username):
        return _json_error("invalid-username")
    if not password:
        return _json_error("missing-password")
    smb_cmd = ["smbpasswd", "-s", username]
    ok, out, err = _run_root_cmd_input(smb_cmd, f"{password}\n{password}\n")
    if not ok:
        return _json_error("smbpasswd-failed", detail=err or out)
    return _json_ok()

@bp.route(ADMIN_PREFIX + "/api/nas/shares/create", methods=["POST"])
@login_required
def nas_share_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "").strip()
    if not _validate_share_name(name):
        return _json_error("invalid-share-name")
    if not path or not path.startswith("/"):
        return _json_error("invalid-path")
    state = _load_nas_state()
    state["shares"][name] = {
        "path": path,
        "comment": data.get("comment", ""),
        "read_only": bool(data.get("read_only")),
        "guest_ok": bool(data.get("guest_ok")),
        "valid_users": _normalize_user_list(data.get("valid_users")),
        "admin_users": _normalize_user_list(data.get("admin_users")),
        "write_list": _normalize_user_list(data.get("write_list")),
        "create_mask": data.get("create_mask", ""),
        "directory_mask": data.get("directory_mask", "")
    }
    ok, err = _write_samba_managed_conf(state)
    if not ok:
        return _json_error("write-smbconf-failed", detail=err)
    _save_nas_state(state)
    return _json_ok({"shares": state["shares"]})

@bp.route(ADMIN_PREFIX + "/api/nas/shares/update", methods=["POST"])
@login_required
def nas_share_update():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not _validate_share_name(name):
        return _json_error("invalid-share-name")
    state = _load_nas_state()
    if name not in state["shares"]:
        return _json_error("share-not-found")
    share = state["shares"][name]
    for key in ("path", "comment", "create_mask", "directory_mask"):
        if key in data:
            share[key] = data.get(key, "")
    for key in ("read_only", "guest_ok"):
        if key in data:
            share[key] = bool(data.get(key))
    for key in ("valid_users", "admin_users", "write_list"):
        if key in data:
            share[key] = _normalize_user_list(data.get(key))
    ok, err = _write_samba_managed_conf(state)
    if not ok:
        return _json_error("write-smbconf-failed", detail=err)
    _save_nas_state(state)
    return _json_ok({"shares": state["shares"]})

@bp.route(ADMIN_PREFIX + "/api/nas/shares/delete", methods=["POST"])
@login_required
def nas_share_delete():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not _validate_share_name(name):
        return _json_error("invalid-share-name")
    state = _load_nas_state()
    state["shares"].pop(name, None)
    ok, err = _write_samba_managed_conf(state)
    if not ok:
        return _json_error("write-smbconf-failed", detail=err)
    _save_nas_state(state)
    return _json_ok({"shares": state["shares"]})

@bp.route(ADMIN_PREFIX + "/api/nas/permissions", methods=["POST"])
@login_required
def nas_permissions():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    owner = (data.get("owner") or "").strip()
    group = (data.get("group") or "").strip()
    mode = (data.get("mode") or "").strip()
    if not path or not path.startswith("/"):
        return _json_error("invalid-path")
    if owner or group:
        target = f"{owner}:{group}" if owner or group else ""
        ok, out, err = _run_root_cmd(["chown", "-R", target, path])
        if not ok:
            return _json_error("chown-failed", detail=err or out)
    if mode:
        ok, out, err = _run_root_cmd(["chmod", "-R", mode, path])
        if not ok:
            return _json_error("chmod-failed", detail=err or out)
    return _json_ok()

@bp.route(ADMIN_PREFIX + "/api/nas/mount", methods=["POST"])
@login_required
def nas_mount():
    data = request.get_json(silent=True) or {}
    device = (data.get("device") or "").strip()
    mount_point = (data.get("mount_point") or "").strip()
    fs_type = (data.get("fs_type") or "").strip()
    if not device.startswith("/dev/"):
        return _json_error("invalid-device")
    if not mount_point.startswith("/"):
        return _json_error("invalid-mount-point")
    ok, out, err = _run_root_cmd(["mkdir", "-p", mount_point])
    if not ok:
        return _json_error("mkdir-failed", detail=err or out)
    cmd = ["mount"]
    if fs_type:
        cmd += ["-t", fs_type]
    cmd += [device, mount_point]
    ok, out, err = _run_root_cmd(cmd)
    if not ok:
        return _json_error("mount-failed", detail=err or out)
    state = _load_nas_state()
    state["mount"] = {"device": device, "mount_point": mount_point, "fs_type": fs_type or state.get("mount", {}).get("fs_type", "")}
    _save_nas_state(state)
    return _json_ok({"mount": state["mount"]})

@bp.route(ADMIN_PREFIX + "/api/nas/umount", methods=["POST"])
@login_required
def nas_umount():
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").strip()
    if not target:
        return _json_error("invalid-target")
    ok, out, err = _run_root_cmd(["umount", target])
    if not ok:
        return _json_error("umount-failed", detail=err or out)
    return _json_ok()

@bp.route(ADMIN_PREFIX + "/api/nas/sleep", methods=["POST"])
@login_required
def nas_sleep():
    data = request.get_json(silent=True) or {}
    device = (data.get("device") or "").strip()
    minutes = data.get("minutes", 0)
    try:
        minutes = int(minutes)
    except Exception:
        return _json_error("invalid-minutes")
    if not device.startswith("/dev/"):
        return _json_error("invalid-device")
    value = _hdparm_value_from_minutes(minutes)
    ok, out, err = _run_root_cmd(["hdparm", "-S", str(value), device])
    if not ok:
        return _json_error("hdparm-failed", detail=err or out)
    state = _load_nas_state()
    state["sleep"] = {"device": device, "minutes": minutes}
    _save_nas_state(state)
    return _json_ok({"sleep": state["sleep"], "hdparm_value": value})

@bp.route(ADMIN_PREFIX + "/api/nas/reload", methods=["POST"])
@login_required
def nas_reload():
    ok, err = _write_samba_managed_conf(_load_nas_state())
    if not ok:
        return _json_error("write-smbconf-failed", detail=err)
    ok, out, err = _run_root_cmd(["systemctl", "reload", "smbd.service"])
    if not ok:
        return _json_error("reload-failed", detail=err or out)
    return _json_ok()

@bp.route(ADMIN_PREFIX + "/logout")
@login_required
def admin_logout():
    """管理员退出"""
    session.clear()
    return redirect(url_for("index"))

@bp.route(ADMIN_PREFIX + "/")
@login_required
def admin_dashboard():
    """管理主页"""
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    return render_template("admin_index.html", user=session.get("user"), cpu=cpu, mem=mem)

@bp.route(ADMIN_PREFIX + "/api/command", methods=["POST"])
@login_required
def admin_command():
    """管理员接口：记录命令日志"""
    data = request.json or {}
    cmd = data.get("cmd", "")
    with open("/home/bbdwz/admin_commands.log", "a") as f:
        f.write(f"[{time.ctime()}] {cmd}\n")
    return jsonify({"status": "ok", "received": cmd})

@bp.route(ADMIN_PREFIX + "/api/status")
@login_required
def admin_status():
    return jsonify(_get_status_payload())

@bp.route(ADMIN_PREFIX + "/api/status/stream")
@login_required
def admin_status_stream():
    def stream():
        last_payload = None
        last_send = time.time()
        while True:
            payload = _get_status_payload()
            payload_str = json.dumps(payload, ensure_ascii=True)
            if payload_str != last_payload:
                yield f"data: {payload_str}\n\n"
                last_payload = payload_str
                last_send = time.time()
            elif time.time() - last_send > 10:
                yield ": keep-alive\n\n"
                last_send = time.time()
            time.sleep(1)

    return Response(stream(), mimetype="text/event-stream")

@bp.route("/api/metrics/network", methods=["POST"])
def network_metrics():
    data = request.get_json(silent=True) or {}
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if "," in ip:
        ip = ip.split(",", 1)[0].strip()
    record_network_metrics(data, ip)
    return jsonify({"ok": True})

@bp.route(ADMIN_PREFIX + "/api/visiter_log")
@login_required
def visiter_log():
    lines = _read_log_tail(VISITER_LOG_PATH, max_lines=1000)
    entries = []
    for line in lines:
        parsed = _parse_log_line(line)
        ip = parsed.get("ip", "")
        parsed["location"] = _lookup_geo(ip)
        entries.append(parsed)
    return jsonify({"lines": lines, "entries": entries})
