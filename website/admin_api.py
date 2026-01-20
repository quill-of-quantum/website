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
from collections import deque

bp = Blueprint("admin", __name__)

ADMIN_PREFIX = "/1"
VISITER_LOG_PATH = "/home/bbdwz/projects/website/logs/visiter.log"

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

CLIENT_TIMEOUT_SEC = 120
CLIENT_PATH_TIMEOUT_SEC = 120
CLIENTS_LOCK = threading.Lock()
CLIENTS = {}
REQUESTS_LOCK = threading.Lock()
REQUEST_TIMES = deque()
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

def record_visit(ip, path):
    now = time.time()
    with CLIENTS_LOCK:
        entry = CLIENTS.setdefault(ip, {"last_seen": now, "paths": {}})
        entry["last_seen"] = now
        entry["paths"][path] = now
        _prune_clients_locked(now)

def record_request_timing(duration_ms):
    now = time.time()
    with REQUESTS_LOCK:
        REQUEST_TIMES.append((now, duration_ms))
        _prune_requests_locked(now)
    _record_request_hourly(now, duration_ms)

def _prune_requests_locked(now):
    cutoff = now - REQUEST_WINDOW_SEC
    while REQUEST_TIMES and REQUEST_TIMES[0][0] < cutoff:
        REQUEST_TIMES.popleft()

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
    return jsonify({"lines": lines})
