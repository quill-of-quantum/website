"""Application-wide Socket.IO server instance.

Feature modules register their own events on this shared transport. Keeping the
instance here prevents one feature (for example, chat) from depending on
another feature (for example, game) just to access Socket.IO.
"""

import os
from urllib.parse import urlsplit

from flask_socketio import SocketIO


def _configured_origins():
    return {
        origin.strip().rstrip("/")
        for origin in os.getenv("SOCKETIO_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    }


def _origin_allowed(origin, environ):
    """Apply same-host Origin checks even when TLS ends at another proxy.

    Engine.IO's built-in same-origin check also compares the URL scheme.  Our
    public HTTPS request reaches this host over FRP as HTTP, so that default
    rejects a legitimate ``https://`` browser Origin.  Matching the complete
    host (including a non-default port) retains the CSRF protection while
    allowing that proxy boundary.  Explicit cross-host origins remain opt-in.
    """
    if not origin or not isinstance(origin, str):
        return False

    normalized_origin = origin.rstrip("/")
    if normalized_origin in _configured_origins():
        return True

    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False

    request_host = environ.get("HTTP_HOST", "").split(",", 1)[0].strip()
    return parsed.netloc.casefold() == request_host.casefold()


socketio = SocketIO(
    cors_allowed_origins=_origin_allowed,
    async_mode="gevent",
    serve_client=True,
    max_http_buffer_size=256 * 1024,
)
