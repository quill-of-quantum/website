from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, Response, make_response
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
from datetime import datetime, timedelta
import geoip2.database
import geoip2.errors

from modules.admin.token_store import create_token, delete_token, enable_token, list_tokens, revoke_token
from modules.auth.api import verify_user_password
from modules.auth.user_store import create_user, delete_user, is_admin_user, list_users, update_user_password, user_exists

bp = Blueprint("admin", __name__)

ADMIN_PREFIX = "/1"
VISITER_LOG_PATH = "/home/bbdwz/projects/website/logs/visiter.log"
GEOIP_DB_PATH = "/home/bbdwz/projects/website/data/geoip/GeoLite2-City.mmdb"
PROJECT_ROOT = "/home/bbdwz/projects/website"
MANAGED_SERVICE_UNITS = {
    "computation": "computation.service",
    "gallery": "gallery.service",
    "boardgame": "boardgame.service",
    "tracker": "tracker_scheduler.service",
    "mihomo": "mihomo.service",
    "frpc": "frpc.service",
    "cpolar": "cpolar.service",
    "email": "email-service.service",
    "housing": "housing_tracker.service",
}

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
        if session.get("logged_in") and not user_exists(session.get("user")):
            session.clear()
        if not session.get("logged_in") or not is_admin_user(session.get("user")):
            if request.path.startswith(ADMIN_PREFIX + "/api") or request.path.startswith("/api/"):
                return jsonify({"require_login": True}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

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


def _get_service_enabled_state(service_name):
    systemctl_path = shutil.which("systemctl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not systemctl_path:
        return "no-systemctl"
    try:
        result = subprocess.run(
            [systemctl_path, "show", "-p", "UnitFileState", service_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            return "unknown"
        for line in result.stdout.splitlines():
            if line.startswith("UnitFileState="):
                return line.split("=", 1)[1].strip() or "unknown"
        return "unknown"
    except Exception:
        return "unknown"


def _get_managed_service_state():
    return {
        key: {
            "unit": unit,
            "status": _get_service_status(unit),
            "enabled": _get_service_enabled_state(unit),
        }
        for key, unit in MANAGED_SERVICE_UNITS.items()
    }

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

def _format_bytes(size):
    try:
        size = float(size or 0)
    except Exception:
        size = 0.0
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{int(size)} {units[unit]}"
    return f"{size:.1f} {units[unit]}"

def _storage_item(label, path, kind):
    path = os.path.expanduser(path)
    total = 0
    files = 0
    dirs = 0
    latest_mtime = None
    exists = os.path.exists(path)
    if exists:
        try:
            if os.path.isfile(path):
                total = os.path.getsize(path)
                files = 1
                latest_mtime = os.path.getmtime(path)
            else:
                for dirpath, dirnames, filenames in os.walk(path):
                    dirs += len(dirnames)
                    for filename in filenames:
                        full_path = os.path.join(dirpath, filename)
                        try:
                            stat = os.stat(full_path)
                        except OSError:
                            continue
                        total += stat.st_size
                        files += 1
                        if latest_mtime is None or stat.st_mtime > latest_mtime:
                            latest_mtime = stat.st_mtime
        except Exception:
            exists = False
    return {
        "label": label,
        "path": path,
        "kind": kind,
        "exists": exists,
        "bytes": total,
        "size": _format_bytes(total),
        "files": files,
        "dirs": dirs,
        "mtime": int(latest_mtime) if latest_mtime else None,
    }

def _get_storage_stats():
    items = [
        _storage_item("访问和快捷动作日志", os.path.join(PROJECT_ROOT, "logs"), "log"),
        _storage_item("全部记录数据", os.path.join(PROJECT_ROOT, "data"), "data"),
        _storage_item("上传和缩略图文件", os.path.join(PROJECT_ROOT, "storage"), "storage"),
        _storage_item("项目缓存", os.path.join(PROJECT_ROOT, "cache"), "cache"),
        _storage_item("Map 数据", os.path.join(PROJECT_ROOT, "data/map"), "map"),
        _storage_item("Map 地理编码缓存", os.path.join(PROJECT_ROOT, "data/map/geocode_cache.db"), "map"),
        _storage_item("Map 下载地理数据 SRTM", "~/.cache/srtm", "map"),
        _storage_item("GeoIP 数据库", os.path.join(PROJECT_ROOT, "data/geoip"), "data"),
        _storage_item("Situation 记录", os.path.join(PROJECT_ROOT, "data/situation"), "data"),
        _storage_item("Tracker 记录", os.path.join(PROJECT_ROOT, "data/tracker"), "data"),
        _storage_item("Chat 记录", os.path.join(PROJECT_ROOT, "data/chat"), "data"),
        _storage_item("Weather 记录", os.path.join(PROJECT_ROOT, "data/weather"), "data"),
        _storage_item("Route Creator 数据", os.path.join(PROJECT_ROOT, "data/route_creator"), "data"),
    ]
    total_labels = {
        "访问和快捷动作日志",
        "全部记录数据",
        "上传和缩略图文件",
        "项目缓存",
        "Map 下载地理数据 SRTM",
    }
    total = sum(item["bytes"] for item in items if item["exists"] and item["label"] in total_labels)
    disk = None
    try:
        usage = psutil.disk_usage("/")
        disk = {
            "path": "/",
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": usage.percent,
            "total_size": _format_bytes(usage.total),
            "used_size": _format_bytes(usage.used),
            "free_size": _format_bytes(usage.free),
        }
    except Exception:
        pass
    return {
        "total_bytes": total,
        "total_size": _format_bytes(total),
        "disk": disk,
        "items": items,
    }

def _get_status_payload():
    services = [
        "website.service",
        "nginx.service",
    ]
    service_status = {name: _get_service_status(name) for name in services}
    payload = {
        "timestamp": int(time.time()),
        "services": service_status,
        "managed_services": _get_managed_service_state(),
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
        "network_latency_agg_14d": _get_network_latency_agg_14d(),
        "storage": _get_storage_stats()
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

def _parse_log_time(value):
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _read_log_recent(path, days=7, max_lines=5000):
    cutoff = datetime.now() - timedelta(days=days)
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\n")
                parsed = _parse_log_line(line)
                log_time = _parse_log_time(parsed.get("time", ""))
                if log_time and log_time >= cutoff:
                    rows.append(line)
                    if max_lines and len(rows) > max_lines:
                        rows = rows[-max_lines:]
        rows.reverse()
        return rows
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

@bp.route(ADMIN_PREFIX + "/token")
@login_required
def admin_token():
    return render_template("token.html", user=session.get("user"))


@bp.route(ADMIN_PREFIX + "/api/tokens")
@login_required
def token_list():
    return jsonify({"ok": True, "tokens": list_tokens()})


@bp.route(ADMIN_PREFIX + "/api/tokens/create", methods=["POST"])
@login_required
def token_create():
    data = request.get_json(silent=True) or {}
    token, record = create_token(
        data.get("label"),
        session.get("user") or "admin",
        data.get("scopes") or ["situation:read"],
    )
    return jsonify({"ok": True, "token": token, "record": record})


@bp.route(ADMIN_PREFIX + "/api/tokens/revoke", methods=["POST"])
@login_required
def token_revoke():
    data = request.get_json(silent=True) or {}
    if not revoke_token(data.get("id")):
        return jsonify({"ok": False, "error": "token not found"}), 404
    return jsonify({"ok": True})


@bp.route(ADMIN_PREFIX + "/api/tokens/enable", methods=["POST"])
@login_required
def token_enable():
    data = request.get_json(silent=True) or {}
    if not enable_token(data.get("id")):
        return jsonify({"ok": False, "error": "token not found"}), 404
    return jsonify({"ok": True})


@bp.route(ADMIN_PREFIX + "/api/tokens/delete", methods=["POST"])
@login_required
def token_delete():
    data = request.get_json(silent=True) or {}
    if not delete_token(data.get("id")):
        return jsonify({"ok": False, "error": "token not found"}), 404
    return jsonify({"ok": True})


@bp.route(ADMIN_PREFIX + "/api/users")
@login_required
def admin_user_list():
    return jsonify({"ok": True, "users": list_users()})


@bp.route(ADMIN_PREFIX + "/api/users/create", methods=["POST"])
@login_required
def admin_user_create():
    data = request.get_json(silent=True) or {}
    ok, error = create_user(data.get("username"), data.get("password"), "user")
    if not ok:
        return _json_error(error)
    return _json_ok({"users": list_users()})


@bp.route(ADMIN_PREFIX + "/api/users/password", methods=["POST"])
@login_required
def admin_user_password():
    data = request.get_json(silent=True) or {}
    ok, error = update_user_password(data.get("username"), data.get("password"))
    if not ok:
        return _json_error(error)
    return _json_ok({"users": list_users()})


@bp.route(ADMIN_PREFIX + "/api/users/delete", methods=["POST"])
@login_required
def admin_user_delete():
    data = request.get_json(silent=True) or {}
    ok, error = delete_user(data.get("username"))
    if not ok:
        return _json_error(error)
    return _json_ok({"users": list_users()})


@bp.route("/api/app/login", methods=["POST"])
def app_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")
    if not verify_user_password(username, password):
        return jsonify({"success": False, "status": "error", "error": "用户名或密码错误"}), 401

    label = data.get("device_name") or data.get("label") or "Android App"
    token, record = create_token(label, username, ["situation:read"])
    return jsonify({
        "success": True,
        "status": "ok",
        "token": token,
        "token_type": "Bearer",
        "record": record,
    })

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
    response = make_response(render_template("admin_index.html", user=session.get("user"), cpu=cpu, mem=mem))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response

@bp.route(ADMIN_PREFIX + "/api/command", methods=["POST"])
@login_required
def admin_command():
    """管理员接口：记录命令日志"""
    data = request.json or {}
    cmd = data.get("cmd", "")
    with open(os.path.join(PROJECT_ROOT, "logs", "admin_commands.log"), "a") as f:
        f.write(f"[{time.ctime()}] {cmd}\n")
    return jsonify({"status": "ok", "received": cmd})

@bp.route(ADMIN_PREFIX + "/api/status")
@login_required
def admin_status():
    return jsonify(_get_status_payload())


@bp.route(ADMIN_PREFIX + "/api/services/toggle", methods=["POST"])
@login_required
def admin_service_toggle():
    data = request.get_json(silent=True) or {}
    service_key = str(data.get("service") or "").strip()
    action = str(data.get("action") or "").strip()
    unit = MANAGED_SERVICE_UNITS.get(service_key)
    if not unit:
        return _json_error("invalid-service")
    if action not in ("enable", "disable"):
        return _json_error("invalid-action")

    cmd = ["systemctl", "enable", "--now", unit] if action == "enable" else ["systemctl", "disable", "--now", unit]
    ok, out, err = _run_root_cmd(cmd)
    if not ok:
        return _json_error("systemctl-failed", detail=err or out)

    return _json_ok({
        "service": service_key,
        "unit": unit,
        "status": _get_service_status(unit),
        "enabled": _get_service_enabled_state(unit),
    })

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
    try:
        days = int(request.args.get("days", 7))
    except Exception:
        days = 7
    days = max(1, min(days, 3650))
    lines = _read_log_recent(VISITER_LOG_PATH, days=days, max_lines=5000)
    entries = []
    for line in lines:
        parsed = _parse_log_line(line)
        ip = parsed.get("ip", "")
        parsed["location"] = _lookup_geo(ip)
        entries.append(parsed)
    return jsonify({"days": days, "lines": lines, "entries": entries})
