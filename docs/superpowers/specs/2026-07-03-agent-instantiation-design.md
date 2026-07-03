# Agent Instantiation — Identity-Partitioned Shared Runtime

**Date:** 2026-07-03
**Status:** Approved design (brainstormed with John 2026-07-03)
**Center of gravity:** ollie-hermes-orchestrator (this repo)
**Cross-repo impacts:** ollie-hermes-frontend, ollie-hermes-cortex, ollie-fleet, ollie-hermes-install

---

## 1. Problem

The Ollie platform is multi-user at the frontend (Supabase logins, many users per
customer instance) but single-user at the agent layer. User identity is validated at
nginx and forwarded to the orchestrator (`X-Auth-Email` / `X-Auth-Role`), where it is
used **only** for governance-event logging. Downstream of the orchestrator, Hermes
receives a shared gateway bearer key and no user identity. Consequences today:

- Hermes sessions have no `user_id`. **Any logged-in user can load any other user's
  `sessionId` and read or continue that conversation.** This is a live privacy hole
  the moment a second real user logs into a customer instance.
- Memory/brain (Cortex), skills, env vars, cron jobs, and workspaces are all
  profile-scoped — shared by every user.
- No per-user personalization, no per-user attribution below the audit log, no
  role-based capability differences.

## 2. Decision

**An "agent instance" is a logical tuple — (customer box, profile, user) — not a
process.** Physical topology is unchanged: one Hermes gateway + dashboard per profile
per box, orchestrator at :9123, Cortex sidecar. The **orchestrator becomes the
identity and policy layer**: every agent interaction enters with a user identity
attached, has policy applied (session ownership, RBAC, privacy mode, TRAIGA
guardrails — all at one chokepoint), and exits fully attributed.

Rejected alternatives:

- **Physical per-user Hermes profiles** — process/port/RAM sprawl on a single box;
  fights the shared-brain requirement (team knowledge would need syncing across N
  copies); duplicates guardrail wiring. Kept in the back pocket as a possible premium
  tier (dedicated profile), which this design does not preclude.
- **Frontend-only session filtering** — appearance of privacy without the fact of it;
  the API would still hand any session to any cookie-holder.

## 3. Agent taxonomy: user-scoped and company-scoped agents

Every agent (Hermes profile) declares a scope in its config:

- **`scope: user`** — the personal assistant ("Ollie"). Each member talks to *their*
  Ollie. Per-user feel = per-user session history + per-user memory namespace,
  enforced at the orchestrator. Still one Hermes process; "your Ollie" is a data
  reality, not a process reality.
- **`scope: company`** — shared specialists ("Pam" the office manager, marketing
  agent, prospecting agent). Sessions, memory, and workspace are team-level — closest
  to how everything works today, so specialists need the least change.

**Members talk only to Ollie. Ollie talks to everyone else** (see §7 for the
admin/superadmin exceptions). The frontend becomes a single conversation for members;
the existing multi-agent picker becomes a role-gated admin view.

Product story: *every employee gets their own Ollie; the office staff — Pam,
marketing, prospecting — are shared, and Ollie knows how to work with them.*

### Scaling note (why sole-front-door holds at 100 users)

Hub-and-spoke is a star topology: N users × r requests × (1 + d·k) runs — linear in
users, small constant. A specialist's workload is identical whether a user addressed
it directly or via delegation; the funnel adds one Ollie run in front (~2x
token/latency premium on delegated tasks only). Exponential blowup comes from
agent-to-agent meshes, which this design forbids (§6). Real pressure points at scale
are box sizing (a Fleet tier question — runs are mostly LLM I/O-wait) and specialist
queue depth (solved by pooling, §6).

## 4. Identity flow

- nginx already injects `X-Auth-Email` / `X-Auth-Role` on `/orchestrator-proxy/`
  requests. The auth validator (`src/api/auth_validate.py`) additionally extracts the
  Supabase user UUID (`sub` claim) and returns **`X-Auth-User-Id`**; nginx forwards
  it. The UUID is the key everywhere; email is display metadata.
- The orchestrator threads identity into governance events (already done), session
  ownership (§5), RBAC decisions (§7), memory namespacing (§8), and delegation
  attribution (§6).
- **Hermes stays identity-blind.** No fork, no upstream patch; the orchestrator wraps
  it. This preserves upgrade compatibility with upstream Hermes.

### Single-ingress invariant (Phase 0)

**All run traffic — browser or agent-originated — flows through the orchestrator
run-proxy.** Any path that lets a client reach a Hermes gateway directly (e.g., an
nginx `/gateway-proxy/` rewrite straight to a gateway URL) bypasses both isolation
and the TRAIGA gates, and must be closed or re-pointed at the orchestrator. Anything
that cannot carry identity does not get to talk to an agent.

## 5. Session ownership — "private chats"

New Supabase table `agent_sessions`:

```
id, instance_id, agent_id, hermes_session_id, user_id, created_at, last_active_at, title
```

RLS: a user reads only their own rows; admin visibility is governed by the privacy
policy (§9).

Run-proxy enforcement (fail-closed, same pattern as TRAIGA Gate 1):

- Run with no `session_id` → forward to Hermes, capture the returned `session_id`,
  record ownership.
- Run with a `session_id` → verify the caller owns it (or is permitted by policy);
  otherwise **403 before Hermes is touched**.
- Session listing and message history move to orchestrator endpoints that filter by
  owner. The frontend stops calling the Hermes dashboard `/api/sessions` (which
  returns everyone's threads) and uses the orchestrator endpoints.
- A Hermes session with no ownership row (out-of-band / pre-migration) is
  inaccessible by default. A one-time backfill assigns existing sandbox sessions to
  John.

## 6. Delegation (agent-to-agent)

The genuinely new machinery. One make-or-break rule: **delegation goes through the
orchestrator, on behalf of the user — never gateway-to-gateway.**

- Ollie gets a `delegate` tool → `POST /v1/agents/{specialist}/runs` at the
  orchestrator with on-behalf-of identity attached.
- Guardrails run on delegated traffic (otherwise Gates 1/2 are bypassable by any
  agent that can reach another agent's port).
- Governance events record the attribution chain: `Jane → Ollie → Pam`.
- Specialist work happens in **ephemeral task-scoped sessions**, attributed to the
  originating user. Durable specialist knowledge stays company-scoped.
- Delegation carries a **task brief, not the conversation**: Ollie composes the task
  context; the specialist never receives the user's raw chat history or memory pack.
  This is the firewall preventing private context bleeding into shared-agent state.
- **Delegate for ownership, call tools for information.** Lookups (e.g.,
  `compliance_lookup`) are tools available to Ollie directly; delegation is reserved
  for handoffs to a specialist that owns state/workflow. This keeps A2A volume a
  small fraction of total traffic.
- Which agents may call which is an orchestrator policy row. **Delegation depth is
  capped: Ollie → specialist only; specialists do not chain to each other in v1.** No
  meshes by omission.
- Because specialists are addressed by name through the orchestrator, they are
  **pool-able later** (N workers behind one name, orchestrator load-balances) with
  zero changes to Ollie.

## 7. Roles & RBAC

Three roles, enforced at the orchestrator (`X-Auth-Role` plumbing exists; role
delivery fixed in frontend migration 0009):

- **Member** — talks to their Ollie. No agent picker.
- **Owner/Admin** (customer-side) — their own Ollie, plus audit view per privacy
  policy (§9), plus *optionally* direct chat with company-scoped agents (per-customer
  policy toggle).
- **SuperAdmin** (JNOW operator) — direct chat with any agent, any scope, any
  instance. The existing agent-picker UI becomes this role's view. This formalizes
  the existing Hermes-dashboard-link side-door into the product permission model, and
  is the Operate-tier service delivery mechanism.

Rules:

- Direct admin/superadmin sessions are still **attributed and logged as admin
  sessions** — governance has no blind spot for operators.
- RBAC lives as **policy rows in the orchestrator** — not nginx configs, not frontend
  conditionals.
- No permission matrix in v1: three roles + a handful of capability flags.

## 8. Memory partitioning — "shared brain, private chats"

Three tiers, each living where its scope lives:

- **Company brain** — shared KB (compliance store, Cortex company knowledge,
  specialist workspaces). Owned by company-scoped agents and shared tools. Unchanged.
- **Per-user memory** — what Jane's Ollie knows about Jane: preferences, ongoing
  work, standing context. Cortex gains a user namespace (keyed `profile/user_id`).
  The orchestrator injects the user's memory pack into each Ollie run and routes
  Ollie's memory writes into the correct namespace. Hermes stays identity-blind —
  namespacing happens at the orchestrator/Cortex boundary.
- **Session history** — per-user threads via `agent_sessions` (§5).

## 9. Per-customer privacy switch

Instance-level policy `privacy_mode ∈ {audit_full, break_glass, hard_private}` —
stored with instance config (Fleet-managed, like `hermes_ui_url`), enforced at the
orchestrator's session-read endpoints.

- **`audit_full`** (regulated verticals: real estate, medical): owner/admin can view
  members' threads; full content in governance events.
- **`break_glass`**: private by default; owner access is an explicit, logged unlock
  action.
- **`hard_private`** (low-regulation verticals): only the member reads content;
  governance events store attestations + metadata only.

Vertical defaults, customer-overridable. SuperAdmin support visibility follows the
same switch with break-glass as the floor — "we can't silently read your chats" is a
sales asset in regulated verticals.

## 10. Rollout phases

Each phase ships alone and adds value alone.

- **Phase 0 — Single ingress** (small; required before any second real user).
  Audit and close every browser→gateway and agent→gateway path that bypasses the
  orchestrator run-proxy.
- **Phase 1 — Session ownership.** `agent_sessions` + RLS; run-proxy ownership checks
  (403 fail-closed); orchestrator session-list/history endpoints; frontend switches
  to them; backfill. *Closes the live privacy hole.*
- **Phase 2 — Scope taxonomy + RBAC.** `scope: user|company` in agent config;
  member/owner/superadmin enforcement; frontend: Ollie-only for members, role-gated
  picker for admins.
- **Phase 3 — Delegation.** `delegate` tool; on-behalf-of runs; attribution chain;
  task-scoped specialist sessions; members can no longer address specialists.
- **Phase 4 — Per-user memory.** Cortex user namespace; memory-pack injection; write
  routing.
- **Phase 5 — Privacy switch + audit view.** Policy field in Fleet; owner audit UI;
  break-glass logging.

## 11. Testing spine

Fail-closed throughout:

- No identity → 403.
- Wrong session owner → 403 before Hermes is touched.
- Member addressing a specialist directly → 403.
- Delegation without on-behalf-of identity → rejected.
- `hard_private`: owner read attempt → denied **and logged**.
- Out-of-band Hermes session (no ownership row) → inaccessible.
- Regression: Hermes untouched; existing orchestrator suite (196 tests) keeps
  passing; existing single-user flows unaffected until the frontend switches
  endpoints.

## 12. Requirements traceability

| Requirement (from brainstorm) | Where addressed |
|---|---|
| Conversation isolation | §5 session ownership |
| Personalization / per-user memory | §8 per-user tier |
| Permissions / audit (TRAIGA) | §4 identity, §6 attribution chain, §7 RBAC |
| Concurrency / perf | §3 scaling note, §6 pooling |
| User count "varies wildly" | §2 logical (not physical) instances |
| Shared brain, private chats | §8 three memory tiers |
| Privacy depth per vertical | §9 privacy switch |
| SuperAdmin direct agent access | §7 RBAC |
| User talks only to Ollie | §3 taxonomy, §6 delegation |
