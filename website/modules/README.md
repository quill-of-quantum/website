# Module Layout

Each feature owns its backend code under `modules/<feature>/`.

- `api.py`: Flask routes and request/response handling.
- `service.py`: business logic.
- `storage.py`: database, JSON, log, and file access.
- `client.py`: optional external API clients.

Templates and shared static files currently stay in the project-level `templates/`
and `static/` directories.

Shared infrastructure that is used by multiple features lives in its own
module. `modules/realtime/` owns the application-wide Socket.IO instance;
feature modules such as `chat` and `game` only register their events on it.
