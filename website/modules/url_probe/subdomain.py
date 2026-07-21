from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import re
import socket
import ssl
import threading
import time
import uuid

import requests

from .client import probe_url


COMMON_PREFIXES = [
    "www", "api", "app", "admin", "portal", "login", "auth", "account",
    "mail", "webmail", "smtp", "imap", "pop", "mx", "autodiscover",
    "cdn", "static", "assets", "media", "img", "images", "files", "download",
    "blog", "news", "shop", "store", "help", "support", "docs", "status",
    "dev", "test", "stage", "staging", "demo", "beta", "preview", "sandbox",
    "m", "mobile", "vpn", "remote", "git", "gitlab", "jenkins", "grafana",
    "cloud", "ns1", "ns2", "dns", "ftp", "sso",
]
MAX_CANDIDATES = 100
JOB_TTL_SECONDS = 3600
_jobs = {}
_lock = threading.Lock()


def normalize_domain(value):
    value = str(value or "").strip().lower().rstrip(".")
    if "://" in value:
        from urllib.parse import urlsplit
        value = (urlsplit(value).hostname or "").lower().rstrip(".")
    else:
        value = value.split("/", 1)[0].split(":", 1)[0]
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("域名格式无效") from exc
    if len(value) > 253 or "." not in value or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        value,
    ):
        raise ValueError("请输入有效的根域名，例如 example.com")
    return value


def _resolve(host):
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return []
    return sorted({item[4][0] for item in addresses})


def _public_ips(host):
    return [ip for ip in _resolve(host) if ipaddress.ip_address(ip).is_global]


def _valid_name(name, domain):
    name = str(name or "").strip().lower().rstrip(".")
    if name.startswith("*."):
        name = name[2:]
    return name if (name == domain or name.endswith(f".{domain}")) and len(name) <= 253 else None


def _certificate_names(host, domain):
    if not _public_ips(host):
        return set()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=host) as secure:
                certificate = secure.getpeercert()
    except (OSError, ssl.SSLError):
        return set()
    names = set()
    for kind, value in certificate.get("subjectAltName", []):
        if kind == "DNS":
            valid = _valid_name(value, domain)
            if valid:
                names.add(valid)
    return names


def _ct_names(domain):
    response = requests.get(
        "https://crt.sh/",
        params={"q": f"%.{domain}", "output": "json"},
        headers={"User-Agent": "WebsiteSubdomainDiscovery/1.0"},
        timeout=12,
    )
    response.raise_for_status()
    names = set()
    for record in response.json():
        for value in str(record.get("name_value") or "").splitlines():
            valid = _valid_name(value, domain)
            if valid:
                names.add(valid)
    return names


def _payload(job):
    return {
        "job_id": job["id"],
        "kind": "subdomain",
        "state": job["state"],
        "phase": job["phase"],
        "domain": job["domain"],
        "wildcard": job["wildcard"],
        "wildcard_ips": job["wildcard_ips"],
        "candidate_count": job["candidate_count"],
        "completed": len(job["results"]),
        "results": list(job["results"]),
        "warnings": list(job["warnings"]),
    }


def _set_phase(job, phase):
    with _lock:
        job["phase"] = phase
        job["updated_at"] = time.time()


def _run(job):
    domain = job["domain"]
    candidates = {domain: {"输入域名"}}
    try:
        _set_phase(job, "检测泛解析")
        wildcard_sets = []
        for _ in range(2):
            ips = set(_public_ips(f"{uuid.uuid4().hex[:16]}.{domain}"))
            if ips:
                wildcard_sets.append(ips)
        if wildcard_sets:
            job["wildcard"] = True
            job["wildcard_ips"] = sorted(set().union(*wildcard_sets))

        if job["cancelled"]:
            return
        _set_phase(job, "查询证书透明度")
        try:
            for name in _ct_names(domain):
                candidates.setdefault(name, set()).add("证书透明度")
        except (requests.RequestException, ValueError) as exc:
            job["warnings"].append(f"证书透明度查询失败：{exc}")

        if job["cancelled"]:
            return
        _set_phase(job, "读取证书 SAN")
        for host in (domain, f"www.{domain}"):
            for name in _certificate_names(host, domain):
                candidates.setdefault(name, set()).add("证书 SAN")

        if job["cancelled"]:
            return
        _set_phase(job, "探测常见 DNS 名称")
        names = [f"{prefix}.{domain}" for prefix in COMMON_PREFIXES]
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(_public_ips, name): name for name in names}
            for future in as_completed(futures):
                if job["cancelled"]:
                    break
                name = futures[future]
                ips = future.result()
                if ips:
                    candidates.setdefault(name, set()).add("DNS 字典")

        ordered = sorted(candidates, key=lambda name: (name != domain, name))[:MAX_CANDIDATES]
        job["candidate_count"] = len(ordered)
        _set_phase(job, "验证 DNS 与网页")

        def inspect(name):
            ips = _public_ips(name)
            sources = sorted(candidates[name])
            wildcard_match = bool(job["wildcard_ips"] and set(ips) == set(job["wildcard_ips"]))
            result = probe_url(f"https://{name}", 5) if ips else {
                "status": None, "final_url": None, "exists": False, "error": "DNS 无解析",
            }
            if ips and result.get("status") is None:
                http_result = probe_url(f"http://{name}", 5)
                if http_result.get("status") is not None:
                    result = http_result
            return {
                "hostname": name,
                "ips": ips,
                "sources": sources,
                "wildcard_match": wildcard_match,
                "url": result.get("final_url") or f"https://{name}",
                "status": result.get("status"),
                "exists": result.get("exists", False),
                "error": result.get("error"),
            }

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(inspect, name): name for name in ordered}
            for future in as_completed(futures):
                if job["cancelled"]:
                    break
                result = future.result()
                if result["ips"] and not (
                    result["wildcard_match"] and result["sources"] == ["DNS 字典"]
                ):
                    with _lock:
                        job["results"].append(result)
                        job["results"].sort(key=lambda item: item["hostname"])
                        job["updated_at"] = time.time()
    finally:
        with _lock:
            job["state"] = "cancelled" if job["cancelled"] else "completed"
            job["phase"] = "已结束" if job["cancelled"] else "完成"
            job["updated_at"] = time.time()


def start_discovery(value):
    domain = normalize_domain(value)
    job = {
        "id": uuid.uuid4().hex,
        "domain": domain,
        "state": "running",
        "phase": "准备中",
        "wildcard": False,
        "wildcard_ips": [],
        "candidate_count": 0,
        "results": [],
        "warnings": [],
        "cancelled": False,
        "updated_at": time.time(),
    }
    with _lock:
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id in [key for key, item in _jobs.items() if item["updated_at"] < cutoff]:
            _jobs.pop(job_id, None)
        _jobs[job["id"]] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return _payload(job)


def get_discovery(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return _payload(job) if job else None


def cancel_discovery(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        job["cancelled"] = True
        job["state"] = "cancelled"
        job["updated_at"] = time.time()
        return _payload(job)
