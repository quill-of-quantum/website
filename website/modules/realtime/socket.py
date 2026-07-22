"""Application-wide Socket.IO server instance.

Feature modules register their own events on this shared transport. Keeping the
instance here prevents one feature (for example, chat) from depending on
another feature (for example, game) just to access Socket.IO.
"""

from flask_socketio import SocketIO


socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="gevent",
    serve_client=True,
)
