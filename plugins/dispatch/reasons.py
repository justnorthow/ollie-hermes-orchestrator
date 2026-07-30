"""The plugin's own refusal vocabulary, for failures the server never saw.

Deliberately NOT imported from src/dispatch/types.py. This package runs inside a
Hermes agent process on a box where the orchestrator is a separate service and
its Python package is not importable — an import from src/ would be an
ImportError at plugin load, i.e. no dispatch tools at all.

These names describe failures of the *call to the orchestrator*, which by
definition never reached the server's own vocabulary. Keep them disjoint from
the REASON_* values in src/dispatch/types.py so an operator reading a log can
tell instantly which side produced the refusal; the runbook tabulates both.

The previous single `orchestrator_unreachable` string covered a rotated key, a
wrong ORCHESTRATOR_URL, a slow consult and a dead service alike, which made
every one of them look like the others.
"""

#: The orchestrator answered, and rejected our credentials (401/403). Almost
#: always a rotated or unset ORCHESTRATOR_KEY in this profile's environment.
ORCHESTRATOR_AUTH_FAILED = "orchestrator_auth_failed"

#: The orchestrator answered with some other error status (404, 5xx). The
#: service is up; the route or the request is wrong.
ORCHESTRATOR_ERROR = "orchestrator_error"

#: No answer within the client budget. The orchestrator may still be working —
#: this says nothing about whether the peer answered.
ORCHESTRATOR_TIMEOUT = "orchestrator_timeout"

#: Could not establish a connection at all: nothing listening, DNS failure, a
#: wrong ORCHESTRATOR_URL, or the service being down.
ORCHESTRATOR_UNREACHABLE = "orchestrator_unreachable"
