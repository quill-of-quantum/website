# Module Layout

Each feature owns its backend code under `modules/<feature>/`.

- `api.py`: Flask routes and request/response handling.
- `service.py`: business logic.
- `storage.py`: database, JSON, log, and file access.
- `client.py`: optional external API clients.

Templates and shared static files currently stay in the project-level `templates/`
and `static/` directories.
