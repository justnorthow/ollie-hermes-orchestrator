# ollie-hermes-orchestrator

Host-native daemon that manages Hermes agent profiles (create / update / delete) on behalf of the Ollie dashboard. Runs as a systemd `--user` service alongside the per-profile Hermes gateway and dashboard services.

## Why a separate service?

The dashboard container (`ollie-hermes-frontend`) is a non-root nginx image with no access to `~/.hermes/`, `systemctl --user`, or `docker compose`. The orchestrator runs natively as the same user that owns the Hermes profiles, exposes a small REST API, and is reverse-proxied by the dashboard's nginx at `/orchestrator-proxy/`.

## Install (EC2 / Linux)

```bash
git clone https://github.com/justnorthow/ollie-hermes-orchestrator.git ~/ollie-hermes-orchestrator
bash ~/ollie-hermes-orchestrator/scripts/install.sh
# Edit ~/.config/ollie-orchestrator/.env to paste HERMES_GATEWAY_KEY:
nano ~/.config/ollie-orchestrator/.env
systemctl --user restart ollie-orchestrator
```

Save the generated `ORCHESTRATOR_KEY` shown by the installer — you'll add it to `~/hermes-stack/.env` so the dashboard can authenticate against the orchestrator.

## Endpoints

All under `http://127.0.0.1:9123/v1/...` — bearer-token authenticated via `ORCHESTRATOR_KEY`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | liveness, no auth |
| `GET` | `/v1/agents` | list agents |
| `GET` | `/v1/agents/{id}` | one agent |
| `POST` | `/v1/agents` | create — SSE stream of progress |
| `PATCH` | `/v1/agents/{id}` | update |
| `DELETE` | `/v1/agents/{id}` | delete (hard) |
| `GET` | `/v1/models` | supported model catalog |
| `GET` | `/v1/skills` | available Hermes skills |

## Develop locally

```bash
python -m venv .venv
. .venv/bin/activate          # or .venv/Scripts/activate on Windows
pip install -r requirements.txt
ORCHESTRATOR_KEY=devkey uvicorn src.api.main:app --port 9123 --reload
```

## Test

```bash
. .venv/bin/activate
pytest -v
```

## Spec / design

See `ollie-hermes-frontend/docs/superpowers/specs/2026-05-26-agent-management-ui-design.md` and `docs/superpowers/plans/2026-05-26-agent-management-ui.md` for the design + implementation plan that produced this repo.
