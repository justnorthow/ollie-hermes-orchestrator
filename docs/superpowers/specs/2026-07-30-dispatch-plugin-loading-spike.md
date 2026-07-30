# Spike: how dispatch tools actually reach a Hermes agent

**Date:** 2026-07-30
**Box:** `ollie-sandbox` (178.105.216.167), Hermes Agent v0.19.0 (2026.7.20), upstream `b4f8c491`
**Method:** read-only inspection. Nothing on the box was changed.

This closes two risks the switchable-dispatch spec left open, and opens a third
that is more serious than either. Read this before touching `plugins/dispatch/`.

## 1. Installed plugins DO survive `hermes update` — risk closed, favourably

`hermes plugins install` exists and takes a Git URL or `owner/repo` shorthand.
`_plugins_dir()` in `hermes_cli/plugins_cmd.py:76` resolves to
`get_hermes_home() / "plugins"` — that is `~/.hermes/plugins`, which is
**outside** `~/.hermes/hermes-agent`, the upstream git checkout that
`hermes update` pulls (`hermes update --help`: "Pull the latest changes from git
and reinstall dependencies").

So an installed plugin is not in the tree that gets updated, and survives.

This is the opposite of memory-provider plugins like Cortex, which are copied
*into* the agent tree (`~/.hermes/hermes-agent/plugins/memory/cortex/`) and are
wiped. The `07-patch-cron-brain.sh` tax does not apply to installed plugins.

The cron-brain tax is real and currently active, incidentally: `git status` on
`~/.hermes/hermes-agent` shows ` M cron/scheduler.py` sitting as an uncommitted
local modification right now.

## 2. There is NO generic tool-plugin category — this breaks `plugins/dispatch/`

Every production implementer of `get_tool_schemas()` on the box lives under
`plugins/memory/` — byterover, supermemory, mem0, hindsight, retaindb, honcho,
holographic, cortex, openviking. All ten. Outside `plugins/`, the only
references are in `tests/`.

`get_tool_schemas()` / `handle_tool_call()` / `system_prompt_block()` /
`get_config_schema()` / `initialize(session_id)` is the **`MemoryProvider`**
contract. It is not a general plugin contract.

`plugins/dispatch/DispatchProvider` implements exactly that shape and is not
registered as a memory provider, so **nothing will ever load or call it.** The
code is correct and tested; it simply has no loader.

The spec flagged this as the deciding unknown — "whether Hermes core supports a
generic tool-plugin category, or only `memory`, decides whether `direct` is a day
or a week" — and Task 7 was built without closing it. The answer is: only memory.

The spec's own fallback was to smuggle the tools in as a second memory-category
plugin. That is blocked on any box running Cortex: `config_defaults.py:1549`
describes the setting as "External memory provider plugin (empty = built-in
only)" — one slot, not a list.

## 3. The supported path is MCP — but it cannot carry provenance

Hermes ships first-class MCP support (`hermes mcp add`: "MCP servers provide
additional tools via the Model Context Protocol"). That is the intended way to
give an agent tools, and it is what dispatch should have been built as.

**But an MCP tool call cannot carry the Hermes conversation session id.**

- `tools/mcp_tool.py:4803` — `await server.session.call_tool(tool_name,
  arguments=args)`. Tool arguments and nothing else. No context object, no
  metadata, no session.
- `tools/mcp_tool.py:2762` — `headers = dict(config.get("headers") or {})`.
  Headers come from static server config, fixed when the server is registered,
  not built per call.
- The `_get_session_id` unpacked at `mcp_tool.py:2926` and `:2964` is the **MCP
  transport's own** session id (the SDK's `Mcp-Session-Id` header, see
  `mcp/server/streamable_http.py:345`). It identifies the MCP connection, not the
  Hermes conversation, and Hermes discards it.

What this means for the design:

| Needed | Available over MCP? |
|---|---|
| `agent_id` | **Yes** — static header per MCP server registration, one per profile |
| `session_id` | **No** — nothing per-turn reaches the server |

Provenance resolution is `get_session_owner(agent_id, session_id) -> user_id`.
Without a trustworthy `session_id`, the orchestrator cannot *resolve* the human.
The only way to obtain one over MCP is to have the model pass it as a tool
argument — which is provenance **asserted by the caller**, the exact thing
`src/api/dispatch.py` refuses and the reason dispatch mediates through the
orchestrator at all. A prompt-injected agent could forge it, and the shared
gateway key is not a boundary either, so nothing would be left holding the
property up.

**Dispatch as designed cannot ship over MCP without a different provenance
mechanism.** This is a design change, not a packaging change.

## What is unaffected

The orchestrator half is correct and independent of all of the above:
`src/dispatch/` (types, roster, authority, audit, backends, inflight) and
`src/api/dispatch.py`. It is an HTTP API with tests, and every security property
it enforces still holds for any caller that can present resolvable provenance.

Only the Hermes-side surface — `plugins/dispatch/`, four files — is stranded.

## Options, none yet chosen

1. **Run-id correlation.** The spec's own stated fallback: "If Hermes doesn't
   expose it to plugins, the fallback is orchestrator-side correlation on
   `run_id`." If every agent turn enters through the orchestrator's run-proxy
   (the Phase 0 single-ingress the dispatch runbook already assumes), the
   orchestrator knows which human started the current run for that agent and can
   correlate an MCP tool call arriving during that window. Needs care around
   concurrency and window boundaries, and it should fail closed when ambiguous.
2. **Per-profile MCP credential.** Give each profile's MCP server registration a
   distinct secret in its static headers instead of relying on the shared gateway
   key. That makes *agent* identity resolvable rather than asserted — a real
   improvement over today regardless — but on its own still does not resolve the
   human.
3. **Upstream a tools plugin category** to `NousResearch/hermes-agent`. Real
   plugins do receive `initialize(session_id)`, so this restores the original
   design intact. Clean, but depends on upstream acceptance.

Option 1 is the spec's anticipated path and does not need anyone else's
cooperation. Option 3 is the only one that restores the design as written.

## Do not

- Do not present `plugins/dispatch/` as deployable. It cannot load today.
- Do not work around the missing session id by accepting one as a tool argument
  without redesigning the trust model. That silently converts a resolved
  property into an asserted one and every guarantee downstream of it becomes
  decorative.
