"""ICE server configuration with a coturn-compatible credential hook."""

import base64
import hashlib
import hmac
import os
import secrets
import time


DEFAULT_STUN_URL = "stun:stun.cloudflare.com:3478"


def _csv_env(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def ice_config(_principal_id: str) -> dict:
    """Return browser-ready ICE servers without exposing long-lived secrets."""
    servers = []
    stun_urls = _csv_env("RTC_STUN_URLS", DEFAULT_STUN_URL)
    if stun_urls:
        servers.append({"urls": stun_urls})

    turn_urls = _csv_env("RTC_TURN_URLS")
    turn_secret = os.getenv("RTC_TURN_SHARED_SECRET", "")
    expires_at = None
    if turn_urls and turn_secret:
        ttl = max(60, min(int(os.getenv("RTC_TURN_TTL_SECONDS", "600")), 3600))
        expires_at = int(time.time()) + ttl
        # coturn only needs an expiry prefix. A random suffix avoids exposing a
        # stable website account identifier to the relay or its logs.
        username = f"{expires_at}:{secrets.token_urlsafe(12)}"
        digest = hmac.new(turn_secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
        servers.append({
            "urls": turn_urls,
            "username": username,
            "credential": base64.b64encode(digest).decode("ascii"),
        })

    return {
        "iceServers": servers,
        "turn_available": bool(turn_urls and turn_secret),
        "turn_credentials_expire_at": expires_at,
    }
