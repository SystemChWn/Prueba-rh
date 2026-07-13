# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"Prueba-rh" (Cheong Woon Mexico HR system) is a small, no-build prototype: static HTML pages with inline CSS/JS calling a minimal Flask API backend, deployed via Docker Compose (own Postgres container, no external build tooling, no test suite).

```
backend/    Flask app (app.py), requirements.txt, Dockerfile
frontend/   Static HTML pages, cp.csv, nginx.conf, Dockerfile
db/         init.sql — schema for `registro` and `encuesta_reclutamiento`
docker-compose.yml, .env.example
```

## Running it

Full stack (recommended — this is how it's meant to run, since nginx is what makes the frontend's relative `/api/...` fetches work):

```
cp .env.example .env   # set a real POSTGRES_PASSWORD
docker compose up -d --build
```

Frontend: `http://localhost:3001` · Backend: `http://localhost:8001` · Postgres: internal-only, no host port published (reachable only from the `backend` container on the Docker network).

Backend alone, outside Docker (e.g. quick iteration on `app.py`): `pip install -r backend/requirements.txt && POSTGRES_HOST=localhost POSTGRES_PASSWORD=... python backend/app.py` — reads all DB connection params from env vars (see `get_db_connection()`), defaults to `debug=False` unless `FLASK_DEBUG=true` is set. This still needs a reachable Postgres with the schema from `db/init.sql` applied.

Frontend alone: any static file server pointed at `frontend/` (e.g. `python -m http.server`) works for layout/UI iteration, but `/api/...` calls will 404 without something proxying to the backend — the full Compose stack is the only way to exercise the forms end-to-end. `cp.csv` is fetched client-side via PapaParse, so `registro.html` must be served over HTTP, not opened via `file://`.

## Architecture

Two separate flows, sharing the same visual style (dark theme, header nav bar) but not linked to each other except via `main.html`:

1. **Public candidate flow**: `main.html` → `registro.html` (candidate self-registration form + recruitment-source survey) → `portalrh.html` (hardcoded-credential login: `admin` / `123`, client-side only, no real auth) → `iniciorh.html` (internal RH portal home).
2. **Internal RH portal** (reached only after the fake login): `iniciorh.html`, `ingreso.html`, `cheong-woon.html`, `kronos.html` — these share an identical nav bar (Inicio / Ingresos / Cheong Woon / Kronos / Cerrar Sesión) and are meant to be edited together when changing shared layout/nav/branding, since there's no shared template — each page duplicates the full HTML/CSS.
3. `empleado.html` exists but is currently empty (unimplemented placeholder).

All frontend filenames are lowercase and hyphen-separated on purpose (e.g. `cheong-woon.html`, not `Cheong Woon.html`) — nginx on Linux is case-sensitive and doesn't tolerate spaces well, unlike the Windows/Mac dev machines this was originally built on. Keep new filenames lowercase-hyphenated and keep every page's nav `href`s in sync when adding/renaming a page — there's no shared template to update in one place.

### Frontend → backend contract

Only two endpoints are actually implemented in `backend/app.py`, both under an `/api/` prefix so nginx can proxy all of `/api/` with a single rule:
- `POST /api/guardar-registro` — inserts a candidate registration (from `registro.html`'s `#registroForm`, including a base64 PNG signature captured via `signature_pad`).
- `POST /api/guardar-encuesta` — inserts a recruitment-source survey (also submitted from `registro.html`).
- `GET /api/health` — used by the backend container's Docker healthcheck.

`registro.html` calls these via `postApi()` (a thin `fetch` wrapper) using plain same-origin relative paths — no hardcoded host/IP. This only works because nginx (`frontend/nginx.conf`) reverse-proxies `/api/` to the `backend` service by Docker Compose service name; if you ever run the frontend without that proxy in front, these calls will fail.

`main.html` (`/api/datos-grafica`) and `cheong-woon.html` (`/api/baja-empleado`) call additional endpoints that **do not exist yet** in `backend/app.py` — these calls will fail until corresponding Flask routes are added. Not a regression from the Docker migration — they were already unimplemented before.

### Data

`cp.csv` (in `frontend/`) is a pipe-delimited (`|`) Mexican postal-code dataset, UTF-8 encoded, loaded client-side in `registro.html` to auto-fill colonia/municipio/estado from a CP. `db/init.sql` defines the Postgres schema (`registro`, `encuesta_reclutamiento`) and is applied automatically only on first boot of an empty Postgres volume — later schema changes need a manual `ALTER TABLE` against the running container, there's no migration tool.

## Known rough edges (don't "fix" silently — confirm with the user first)

- `portalrh.html` login is a hardcoded client-side check (`admin`/`123`) with no session/token — anyone can bypass it by navigating directly to `iniciorh.html`. Flagged as a fast-follow, deliberately not touched during the Docker/deploy migration.
- No HTTPS/domain/reverse-proxy-in-front setup — nginx serves plain HTTP on port 3001. If integrating behind an existing shared reverse proxy on the deploy server, point it at `localhost:3001`.
