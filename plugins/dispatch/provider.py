"""Hermes tool plugin exposing agent-to-agent consult.

Installed with `hermes plugins install` — the supported, upgrade-safe path for
tool plugins. It deliberately does NOT go in hermes-agent's bundled tree, which
`hermes update` wipes.

In DISPATCH_MODE=off this plugin contributes no tool schemas and no system-prompt
text, so an agent's context is byte-identical to a box without it installed.
"""
import json
import os

from .http_client import DispatchHttpClient

_PROMPT_BLOCK = """\
You can consult teammate agents on this box.

- `list_teammates` shows who is available and whether each can be consulted inline.
- `ask_teammate` asks one of them a question and returns their answer.

Rules that are not negotiable:
- NEVER invent or paraphrase a teammate's answer. If the tool returns a refusal,
  say what it says. An answer you did not receive is indistinguishable from one
  you did, to the person reading your reply.
- NEVER say work was handed off, assigned, or sent to a teammate. This tool only
  asks questions and returns answers; it cannot give anyone work.
- If a teammate cannot be consulted inline, name them and the exact ask to your
  human instead.
"""


class DispatchProvider:
    # Modes this plugin actually implements a tool-call path for. Anything else
    # -- typos, or a real-but-not-yet-implemented mode like "local"/"linear" --
    # behaves as off from the plugin's perspective, even though the server would
    # accept it as a VALID_MODES value and refuse each call with not_enabled.
    # Advertising tools for a mode we can't drive would mean every call the
    # agent makes gets refused server-side, having led it on client-side.
    # Deliberately not imported from src/: the plugin must stand alone on a
    # Hermes box where the orchestrator package is not importable.
    _IMPLEMENTED_MODES = frozenset({"direct"})

    def __init__(self):
        self._session_id = ""
        self._client = DispatchHttpClient()

    @property
    def name(self) -> str:
        return "dispatch"

    def is_available(self) -> bool:
        return self._mode() != "off"

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id

    @classmethod
    def _mode(cls) -> str:
        raw = os.environ.get("DISPATCH_MODE", "off").strip() or "off"
        return raw if raw in cls._IMPLEMENTED_MODES else "off"

    @staticmethod
    def _agent_id() -> str:
        return os.environ.get("DISPATCH_AGENT_ID", "")

    def get_config_schema(self) -> list[dict]:
        return [
            {"key": "mode", "label": "Dispatch mode",
             "type": "select", "options": ["off", "direct"], "default": "off"},
            {"key": "orchestrator_url", "label": "Orchestrator URL",
             "type": "string", "default": "http://127.0.0.1:9123"},
        ]

    def system_prompt_block(self) -> str:
        return "" if self._mode() == "off" else _PROMPT_BLOCK

    def get_tool_schemas(self) -> list[dict]:
        if self._mode() == "off":
            return []
        return [
            {
                "name": "list_teammates",
                "description": (
                    "List the other agents on this box and whether each can be "
                    "consulted inline."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "ask_teammate",
                "description": (
                    "Ask one teammate agent a question and get their answer in "
                    "this turn. Use for questions, not for giving work."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "teammate": {"type": "string",
                                     "description": "agent_id from list_teammates"},
                        "question": {"type": "string"},
                    },
                    "required": ["teammate", "question"],
                },
            },
        ]

    def handle_tool_call(self, name: str, args: dict) -> str:
        # Self-gated, not just unadvertised: get_tool_schemas()/system_prompt_block()
        # hide the tools in off mode, but a stale client-side tool cache or a host
        # bug can still invoke handle_tool_call directly. Refusing here -- before
        # any HTTP call -- is what makes the module docstring's "byte-identical to
        # a box without it installed" claim true rather than aspirational.
        if self._mode() == "off":
            return json.dumps({
                "ok": False,
                "reason": "not_enabled",
                "detail": "dispatch is disabled on this instance (DISPATCH_MODE=off)",
            })
        if name == "list_teammates":
            return self._list_teammates()
        if name == "ask_teammate":
            return self._ask_teammate(args)
        return f"Unknown dispatch tool: {name}"

    def _list_teammates(self) -> str:
        try:
            data = self._client.get("/v1/dispatch/teammates",
                                    {"agent": self._agent_id()})
        except Exception as exc:  # noqa: BLE001 — never raise into the model
            return json.dumps({"ok": False, "reason": "orchestrator_unreachable",
                               "detail": str(exc)})
        return json.dumps(data)

    def _ask_teammate(self, args: dict) -> str:
        payload = {
            "from_agent": self._agent_id(),
            "session_id": self._session_id,
            "to_agent": args.get("teammate", ""),
            "question": args.get("question", ""),
            "chain": [],
        }
        try:
            data = self._client.post("/v1/dispatch/consult", payload)
        except Exception as exc:  # noqa: BLE001 — never raise into the model
            return json.dumps({"ok": False, "reason": "orchestrator_unreachable",
                               "detail": str(exc)})
        return json.dumps(data)
