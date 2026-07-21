import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests


USER_AGENT = "WebsiteUrlProbe/1.0"
MAX_REDIRECTS = 5


class UnsafeUrlError(ValueError):
    pass


def normalize_url(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("URL 不能为空")
    if "://" not in value:
        value = f"https://{value}"

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持有效的 HTTP/HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不能包含用户名或密码")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL 端口无效") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL 端口无效")
    return parsed.geturl()


def ensure_public_url(url):
    parsed = urlsplit(normalize_url(url))
    host = parsed.hostname
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"域名无法解析：{host}") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeUrlError("出于安全考虑，不能探测本机、局域网或保留地址")
    return parsed.geturl()


def probe_url(url, timeout):
    current = normalize_url(url)
    started_url = current
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}

    try:
        current = ensure_public_url(current)
        for redirect_count in range(MAX_REDIRECTS + 1):
            response = requests.head(
                current,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
            if response.status_code in {405, 501}:
                response.close()
                response = requests.get(
                    current,
                    headers={**headers, "Range": "bytes=0-0"},
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )

            status = response.status_code
            location = response.headers.get("Location")
            response.close()
            if status in {301, 302, 303, 307, 308} and location:
                if redirect_count >= MAX_REDIRECTS:
                    raise requests.TooManyRedirects("重定向次数过多")
                current = ensure_public_url(urljoin(current, location))
                continue

            exists = 200 <= status < 400 or status in {401, 403}
            return {
                "url": started_url,
                "final_url": current,
                "status": status,
                "exists": exists,
                "error": None,
            }
    except (requests.RequestException, ValueError) as exc:
        return {
            "url": started_url,
            "final_url": current,
            "status": None,
            "exists": False,
            "error": str(exc) or exc.__class__.__name__,
        }
