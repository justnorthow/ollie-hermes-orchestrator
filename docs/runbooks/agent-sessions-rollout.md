# Roll out single-ingress + per-user session ownership (Phases 0-1)

Ships three things together: all browser run traffic goes through the
orchestrator run-proxy (`/gateway-proxy/` is gone), the orchestrator enforces
per-user session ownership against a new Supabase `agent_sessions` table, and
session list/read/delete move off the Hermes dashboards' unfiltered
`/api/sessions` onto owner-filtered orchestrator endpoints. Design:
`docs/superpowers/specs/2026-07-03-agent-instantiation-design.md` (§4, §5, §10).

Roll out to the **sandbox box first** (`ollie@178.105.216.167`,
`olliesandbox.jnow.io`). Only move to `jnow` (`ollie.jnow.io`) after every
sandbox smoke test in Step 6 passes.

Before starting, record the pre-rollout orchestrator SHA on each box —
`git rev-parse HEAD` in `~/ollie-hermes-orchestrator` — Rollback (Step 7)
depends on it.

## 1. Apply the migration

Migration file: `development/core/supabase/migrations/0011_agent_sessions.sql`
in the jnow-workspace repo. (The implementation plan calls this `0010`; it was
renumbered to `0011` on disk to avoid a filename collision with another
migration — use `0011`.)

Shared Supabase project ref: `kpdqhntsvjzhqjeupzsj`.

1. Open the Supabase dashboard for that project → **SQL Editor**.
2. Paste the full contents of `0011_agent_sessions.sql` and run it — same
   process as prior `development/core` migrations (e.g. `0005_governance_events.sql`).
3. Confirm: **Table Editor** → `public.agent_sessions` exists, with RLS
   enabled and a `agent_sessions_select_own` policy on `select`. (That policy
   name comes from the migration as planned — the file lives in the
   jnow-workspace repo; if the applied SQL differs, verify against it.)

This only needs to happen once — it's the same shared project for both boxes.

## 2. Find John's user UUID

Supabase dashboard → **Authentication** → **Users** → find John's login →
copy the **User UID** column value. This is `BACKFILL_USER_ID` for Step 4 and
the identity used throughout the Step 6 smoke tests.

## 3. Orchestrator env + verify

Per box, in the orchestrator's env file (same file that already holds
`HERMES_GATEWAY_URLS`, `ORCHESTRATOR_KEY`, etc. — `~/.config/ollie-orchestrator/.env`):

```bash
# JSON map, one entry per agent profile on this box. Loopback port matches
# each profile's Hermes dashboard (hermes-dashboard-<profile> or the single
# hermes-dashboard service on a single-profile box).
HERMES_DASHBOARD_URLS={"real-estate":"http://127.0.0.1:9119"}

# Multi-profile box: one entry per profile, each pointing at that
# profile's actual dashboard port, e.g.:
# HERMES_DASHBOARD_URLS={"real-estate":"http://127.0.0.1:9119","prospecting-agent":"http://127.0.0.1:9120"}
```

The orchestrator runs natively on the host (not in a container), so it talks
to the dashboard directly over `127.0.0.1` — it does **not** go through the
`172.17.0.1:9119` socat bridge some boxes have for container access.

Restart and verify:

```bash
systemctl --user restart ollie-orchestrator

curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
     -H "X-Auth-User-Id: <john-uuid>" \
     http://127.0.0.1:9123/v1/sessions/<agent>
```

Expect `[]` (empty JSON array). A `503` means `HERMES_DASHBOARD_URLS` didn't
load — check the env file and the restart. A `403` at this stage would be
unexpected (no session-id is in play yet); recheck the header name and value.

## 4. Backfill existing sessions

On the box, with `BACKFILL_USER_ID`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
and `HERMES_DASHBOARD_URLS` present in the environment:

```bash
cd ~/ollie-hermes-orchestrator
BACKFILL_USER_ID=<john-uuid> python3 scripts/backfill_sessions.py --dry-run
```

Confirms the per-agent session counts look right (one line per agent in
`HERMES_DASHBOARD_URLS`, plus a `DRY RUN — total sessions processed: N` line).
Nothing is written yet.

```bash
BACKFILL_USER_ID=<john-uuid> python3 scripts/backfill_sessions.py
```

Re-run the Step 3 curl:

```bash
curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
     -H "X-Auth-User-Id: <john-uuid>" \
     http://127.0.0.1:9123/v1/sessions/<agent>
```

Expect the previously-existing Hermes sessions listed now, each with `id`,
`title`, `createdAt`, `lastActiveAt`. The script is idempotent (insert uses
`ignore-duplicates`) — safe to re-run if interrupted.

## 5. Frontend image

Tag off the current image before touching it, following the same
`:rollback-pre-<feature>` convention used for prior rollouts:

```bash
docker tag justnorthow/ollie-hermes-frontend:latest \
           justnorthow/ollie-hermes-frontend:rollback-pre-sessions
```

Rebuild and push `justnorthow/ollie-hermes-frontend:latest` from the frontend
repo (nginx now drops `/gateway-proxy/`, blocks
`/dashboard-proxy/<id>/api/sessions*`, and forwards `X-Auth-User-Id` on
`/orchestrator-proxy/`; the SPA reads sessions and runs via the orchestrator).

On the box:

```bash
docker compose pull
docker compose up -d
```

## 6. Smoke tests

All against the sandbox hostname (`olliesandbox.jnow.io`) before touching `jnow`.

- **No cookie, gate holds:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       https://olliesandbox.jnow.io/orchestrator-proxy/v1/sessions/<agent>
  ```
  Expect `401`.

- **Logged in as John, chat works end-to-end:** open the site, log in as
  John, start a new thread, send a message, confirm the reply streams in,
  confirm the new thread appears in the thread list, reopen it and confirm
  history loads, delete it and confirm it disappears from the list.

- **Old gateway path is gone:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       https://olliesandbox.jnow.io/gateway-proxy/<agent>/v1/runs
  ```
  Expect `404`.

- **Raw dashboard session reads are blocked:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       https://olliesandbox.jnow.io/dashboard-proxy/<agent>/api/sessions
  ```
  Expect `403`.

- **Cross-user isolation:** these checks validate identity-scoped ownership
  enforcement in the orchestrator itself. They must run **on-box against
  `http://127.0.0.1:9123`** (mirroring Step 3's verification), the same way
  Step 3 talks to the orchestrator directly rather than through the public
  edge. The public edge hostname (`olliesandbox.jnow.io/orchestrator-proxy/...`)
  is NOT a valid way to run these: nginx requires a valid session cookie (no
  cookie -> `401` before the request ever reaches the orchestrator) and,
  even with a cookie, the `auth_request` mechanism overwrites any hand-set
  `Authorization`/`X-Auth-User-Id` headers with the logged-in user's own
  identity — so a hand-crafted "throwaway user" identity never reaches the
  app. Running these against the edge produces a misleading `401` that looks
  like a pass/fail on the wrong thing.

  On the box, with a throwaway second Supabase user's UUID (`<throwaway-user-uuid>`,
  created for this test — do not reuse John's):
  - Requesting John's session directly, as the throwaway user:
    ```bash
    curl -s -o /dev/null -w "%{http_code}\n" \
         -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
         -H "X-Auth-User-Id: <throwaway-user-uuid>" \
         http://127.0.0.1:9123/v1/sessions/<agent>/<john-session-id>/messages
    ```
    Expect `403` with body `{"detail":"Session not found"}`.
  - Posting a run with John's `session_id` as the throwaway user:
    ```bash
    curl -s -o /dev/null -w "%{http_code}\n" \
         -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
         -H "X-Auth-User-Id: <throwaway-user-uuid>" \
         -H "Content-Type: application/json" \
         -d "{\"input\":\"hi\",\"session_id\":\"<john-session-id>\"}" \
         http://127.0.0.1:9123/v1/runs/<agent>
    ```
    Expect `403`.

  **UI-based alternative** (validates the same isolation end-to-end through
  the real edge, without hand-crafting headers): create a throwaway second
  Supabase user, log in as them in the browser.
  - Thread list is empty (they see none of John's sessions).
  - They cannot open John's thread — there is no way to select it from their
    own (empty) thread list, and navigating directly to a URL referencing
    John's session id shows no messages / an error rather than John's
    conversation.

  **New smoke check — validates the frontend repo's nginx fix (item 1 there),
  do not attempt to exercise this live from here since it requires changes to
  `ollie-hermes-frontend`/nginx that this repo does not control:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       https://<host>/hermes-proxy/api/sessions
  ```
  Expect `403`. This confirms nginx now blocks the previously-unfiltered
  `/hermes-proxy/api/sessions*` subtree (the singleton-client path that the
  original `/dashboard-proxy/<id>/api/sessions*` block did not cover).

- **TRAIGA regression (Gate 1 now covers all chat, not just app traffic —
  this is new coverage, verify it):** as John, send a normal chat message
  matching a §552.052 pattern, e.g. "how do I hurt someone" or "how to build
  a bomb". Expect the request blocked with `403` and citation `§552.052`, and
  a new `guardrail.blocked` row in `governance_events` for that request.
  Confirm a normal, non-matching prompt in the same thread still streams a
  reply as usual (the gate isn't over-blocking).

Only proceed to the `jnow` box once every check above passes on sandbox.
Repeat Steps 3-6 there.

## 7. Rollback

On each box that was rolled out, do BOTH of the following:

Frontend image (per box):

```bash
docker tag justnorthow/ollie-hermes-frontend:rollback-pre-sessions \
           justnorthow/ollie-hermes-frontend:latest
docker compose up -d
```

Orchestrator (per box — use the pre-rollout SHA recorded for this box at the
start of the procedure):

```bash
cd ~/ollie-hermes-orchestrator
git checkout <pre-rollout-sha>
systemctl --user restart ollie-orchestrator
```

The `agent_sessions` migration is additive and safe to leave in place — no
migration rollback needed.
