# Phase 2a.2 — Gated Per-Agent Dashboard Management Surface

**Date:** 2026-07-03
**Status:** Approved design (brainstormed with John 2026-07-03)
**Parent:** `docs/superpowers/specs/2026-07-03-rbac-scope-taxonomy-phase2a-design.md` (Phase 2a) + its final-review finding on the management plane
**Center of gravity:** ollie-hermes-orchestrator
**Cross-repo impacts:** ollie-hermes-frontend (management client repoint, agent selector, nav gating, nginx edge block)

---

## 1. Problem

Phase 2a gated who may *chat with* an agent and 2a.1 gated the agent *lifecycle* API
(`/v1/agents*`). But the Hermes dashboard **management** surface — skills, cron
jobs (Schedules), config, env (which may hold secrets), model selection, profiles,
plugins, logs, usage — is still reachable by any authenticated user through
`/hermes-proxy/api/*` (the singleton dashboard) and `/dashboard-proxy/<id>/api/*`,
with no tier gate (only `/api/sessions` was edge-blocked in Phase 1). So a `member`
can read another agent's env secrets and reconfigure its skills/cron/config. The
in-app Schedules/Skills/Settings pages are real features that use these endpoints,
so a blunt edge block would break admin functionality.

## 2. Decision

Give the management surface the same architectural treatment Phase 1 gave sessions:
**route it through the orchestrator, gated `account_admin+`, and block the raw
browser paths.** Management becomes **per-agent** (each agent's dashboard is a
distinct port, already in `HERMES_DASHBOARD_URLS`), fronted by a shared
"Managing: [agent]" selector. Members keep chat, their own review/approvals, and
benign status polling; everything else is admin-only. Enforcement is server-side
(`admin_denied`); the frontend nav hiding is UX only.

## 3. Orchestrator: generic per-agent management proxy

One generic endpoint (new module `src/api/manage.py`):

```
ALL /v1/agents/{agent}/dashboard/{subpath:path}
```

Per request:

1. **Gate:** `authz.admin_denied(request)` — account_admin+ or `403 Forbidden`
   (identity-less internal callers pass, per the established trust boundary).
2. **Allowlist (prefix-based, using the REAL dashboard paths):** `subpath` must
   equal an allowed prefix or start with `prefix + "/"`. Allowed prefixes —
   verified against `HermesDashboardClient`'s actual `/api/*` calls:
   `skills`, `cron`, `config`, `env`, `model`, `profiles`, `logs`,
   `analytics` (usage is `/api/analytics/usage`), `dashboard/plugins`
   (plugins is `/api/dashboard/plugins`, NOT `/api/plugins`), `providers/oauth`
   (the OAuth provider catalog/connection status). Anything else → `404` (no
   existence leak). Prefix-with-`/` matching blocks traversal (`../`) and blocks
   `sessions`/`status` (their own routes). Note `dashboard/plugins` is the exact
   allowed prefix — do NOT allow bare `dashboard` (too broad).
3. **Forward:** proxy the original METHOD + request body + query string to
   `HERMES_DASHBOARD_URLS[agent]` + `/api/{subpath}`, adding the
   `X-Hermes-Session-Token` header (from `HERMES_DASHBOARD_TOKEN`, wired in Phase
   1's `sessions._dashboard_headers`). 503 if the agent's dashboard base is
   unconfigured.
4. **Return** the upstream status + body verbatim (`Response`, media type from
   upstream or JSON).

Reuse the Phase 1 dashboard plumbing: `sessions._dashboard_base(agent)` and the
token header helper (extract a shared `_dashboard_headers()` if not already
importable). Keep the proxy in its own module so its one responsibility (gated
management forwarding) is testable in isolation.

**Benign status passthrough (member-reachable):** a small separate route
`GET /v1/agents/{agent}/status` — NOT admin-gated (any authenticated caller;
identity-less passes too) — forwards `GET /api/status` with the session token.
Chat polling depends on it. It is deliberately outside the management proxy so the
allowlist stays admin-only.

## 4. nginx edge block (frontend repo)

Block the raw management subtrees on both proxy prefixes so the only path in is the
gated orchestrator proxy (mirrors Phase 1's `/api/sessions` block):

- `location ~ ^/hermes-proxy/api/(skills|cron|config|env|model|profiles|logs|analytics|dashboard/plugins|providers/oauth) { return 403; }`
- `location ~ ^/dashboard-proxy/[^/]+/api/(skills|cron|config|env|model|profiles|logs|analytics|dashboard/plugins|providers/oauth) { return 403; }`

(Regex covers the same real paths as the §3 allowlist — note `dashboard/plugins`
and `providers/oauth` are the nested ones; bare `dashboard`/`providers` are NOT
blocked wholesale, only those subpaths.)

`/api/status` and `/api/sessions` are NOT blocked (status stays member-reachable;
sessions already route through the orchestrator and its `/api/sessions` subtree was
blocked in Phase 1). Add via the existing `generate-nginx.sh` emission + the static
`nginx.conf`, matching the Phase 1 pattern; extend `tests/generate-nginx.test.ts`
to assert the new blocks appear and precede the proxy `location` blocks.

## 5. Frontend: per-agent management, selector, nav gating

- **Client repoint:** `HermesDashboardClient`'s MANAGEMENT methods (skills, cron,
  config, env, model, profiles, plugins, logs, usage) target
  `/orchestrator-proxy/v1/agents/{agent}/dashboard/{subpath}` for a given agent id
  instead of the singleton `/hermes-proxy`. Session methods are unchanged (already
  routed in Phase 1). `getStatus` targets the new
  `/v1/agents/{agent}/status`. Simplest shape: the management client is constructed
  per selected-agent (like the Phase 1 per-agent dashboard clients), or the methods
  take an agent id.
- **"Managing: [agent]" selector:** a shared control at the top of the management
  section; options from `whoami.reachableAgentIds` (admins see all); defaults to the
  default/Ollie agent. The management pages read the selection and call the client
  for that agent.
- **Nav gating:** from `whoami.tier`, render the management nav items
  (`skills, schedules, settings, logs, env, usage, models, profiles, plugins,
  memory_providers`) only for `account_admin+`. Members see `chat` + their own
  `review`. Fail-open UI (whoami down → conservative nav; the orchestrator 403 is
  the real gate).

## 6. Testing spine (fail-closed)

- Member → any `/v1/agents/{agent}/dashboard/*` → 403 before the dashboard is
  touched.
- Admin → allowlisted subpath (`env`, `cron/jobs`, `skills/x`) → forwarded with the
  session token → upstream status returned; METHOD + body + query preserved (a PUT
  `/env` body reaches the dashboard as a PUT with that body).
- Admin → non-allowlisted subpath (`sessions`, `../etc`, `dashboard/plugins`
  outside the allowlist set) → 404, dashboard never contacted.
- Identity-less internal caller → allowed.
- `GET /v1/agents/{agent}/status` → reachable by a member (200), forwards the
  token.
- nginx: `/hermes-proxy/api/env` + `/dashboard-proxy/<id>/api/skills` → 403;
  `/api/status` still reachable; blocks precede the proxy locations.
- Frontend: member nav omits management items; admin nav includes them; the selector
  drives the proxy agent id; management calls hit the orchestrator path, not
  `/hermes-proxy`.

## 7. Rollout

Folds into the Phase 2a deploy (neither is on a box yet). No new box config: the
`HERMES_DASHBOARD_TOKEN` from Phase 1 already covers the proxy's dashboard auth, and
the frontend rebuild is the same image bump. Add the §6 smoke tests to
`docs/runbooks/rbac-phase2a-rollout.md` so all of Phase 2 (2a + 2a.1 + 2a.2) deploys
sandbox-first as one pass. Rollback: retag image + `git checkout` orchestrator +
restart; nothing persistent added.

## 8. Task decomposition (~6)

1. Orchestrator management proxy module (`manage.py`): gated allowlist proxy +
   status passthrough + router wiring in main.py.
2. nginx edge block (generate-nginx.sh + nginx.conf) + generation test.
3. Frontend `HermesDashboardClient` management-method repoint to the per-agent
   orchestrator path (+ status route).
4. "Managing: [agent]" selector wired to the management pages.
5. whoami-driven nav gating for management items.
6. Runbook smoke-test additions (fold into the 2a runbook).

## 9. Requirements traceability

| Requirement (brainstorm) | Section |
|---|---|
| Route management through the orchestrator, gated account_admin+ | §3 |
| Generic allowlist proxy (not per-endpoint enumeration) | §3 |
| Status stays member-reachable | §3 |
| Block raw /hermes-proxy + /dashboard-proxy management paths | §4 |
| Per-agent management + "Managing: [agent]" selector | §5 |
| Hide management nav for members (whoami tier) | §5 |
| Fail-closed gate, fail-open UI | §3, §5 |
| Folds into the Phase 2a rollout | §7 |
