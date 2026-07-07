import scripts.backfill_sessions as backfill
from scripts.backfill_sessions import rows_from_sessions


def test_backfill_sends_dashboard_session_token(monkeypatch):
    """The dashboard 401s without X-Hermes-Session-Token even on loopback; the
    backfill's /api/sessions GET must forward HERMES_DASHBOARD_TOKEN."""
    monkeypatch.setenv("BACKFILL_USER_ID", "uuid-john")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", '{"real-estate":"http://127.0.0.1:9122"}')
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "tok-123")
    captured = {}

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _Resp([])  # no sessions -> no POST attempted

    monkeypatch.setattr(backfill.httpx, "get", fake_get)
    rc = backfill.main()
    assert rc == 0
    assert captured["headers"]["X-Hermes-Session-Token"] == "tok-123"


def test_backfill_post_is_idempotent_upsert(monkeypatch):
    """Regression lock: the backfill POST must always carry the on_conflict
    upsert target + ignore-duplicates Prefer header. This is what makes the
    one-time backfill safe to re-run — it never double-inserts a session and
    never overwrites an existing owner. If a future edit drops these, this
    test must fail."""
    monkeypatch.setenv("BACKFILL_USER_ID", "u-1")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", '{"default":"http://localhost:9119"}')
    captured = {}

    class _Resp:
        def json(self):
            return [{"id": "s-1", "title": "t"}]

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp()

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        captured.update(params=params, headers=headers, json=json)
        return _Resp()

    monkeypatch.setattr(backfill.httpx, "get", fake_get)
    monkeypatch.setattr(backfill.httpx, "post", fake_post)
    assert backfill.main() == 0
    # The idempotency guarantee: never double-insert, never overwrite an owner.
    assert captured["params"]["on_conflict"] == "agent_id,hermes_session_id"
    assert captured["headers"]["Prefer"] == "resolution=ignore-duplicates,return=minimal"


def test_rows_from_sessions_maps_and_skips_blank_ids():
    sessions = [
        {"id": "s-1", "title": "Hello"},
        {"id": "", "title": "bad"},
        {"title": "no id"},
        {"id": "s-2"},
    ]
    rows = rows_from_sessions("real-estate", sessions, "uuid-john")
    assert rows == [
        {"agent_id": "real-estate", "hermes_session_id": "s-1", "user_id": "uuid-john", "title": "Hello"},
        {"agent_id": "real-estate", "hermes_session_id": "s-2", "user_id": "uuid-john", "title": None},
    ]


def test_rows_from_sessions_maps_camelcase_timestamps():
    """Hermes dashboard /api/sessions items carry createdAt/updatedAt (camelCase,
    per HermesSession in the frontend). These must map to created_at/last_active_at
    so the backfilled thread list preserves real chronological order."""
    sessions = [
        {"id": "s-1", "title": "Hello", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z"},
    ]
    rows = rows_from_sessions("real-estate", sessions, "uuid-john")
    assert rows == [
        {"agent_id": "real-estate", "hermes_session_id": "s-1", "user_id": "uuid-john", "title": "Hello",
         "created_at": "2026-01-01T00:00:00Z", "last_active_at": "2026-01-02T00:00:00Z"},
    ]


def test_rows_from_sessions_maps_snakecase_timestamp_variants():
    """Defensive fallback for snake_case source field names."""
    sessions = [
        {"id": "s-1", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"},
        {"id": "s-2", "started_at": "2026-01-03T00:00:00Z", "last_active": "2026-01-04T00:00:00Z"},
    ]
    rows = rows_from_sessions("real-estate", sessions, "uuid-john")
    assert rows[0]["created_at"] == "2026-01-01T00:00:00Z"
    assert rows[0]["last_active_at"] == "2026-01-02T00:00:00Z"
    assert rows[1]["created_at"] == "2026-01-03T00:00:00Z"
    assert rows[1]["last_active_at"] == "2026-01-04T00:00:00Z"


def test_rows_from_sessions_omits_timestamp_keys_when_absent():
    """No source timestamp field present/valid -> omit the keys entirely so DB
    defaults (now()) apply, rather than forcing a value."""
    sessions = [{"id": "s-1", "title": "no timestamps"}]
    rows = rows_from_sessions("real-estate", sessions, "uuid-john")
    assert "created_at" not in rows[0]
    assert "last_active_at" not in rows[0]


def test_rows_from_sessions_ignores_non_string_timestamp_values():
    """A malformed (non-string) timestamp field is treated as absent."""
    sessions = [{"id": "s-1", "createdAt": 12345, "updatedAt": None}]
    rows = rows_from_sessions("real-estate", sessions, "uuid-john")
    assert "created_at" not in rows[0]
    assert "last_active_at" not in rows[0]


def test_rows_from_sessions_stamps_instance_id_when_given():
    """Instance scoping (cross-instance session bleed fix, 2026-07-07): the
    orchestrator's reads filter agent_sessions by INSTANCE_ID, so backfilled
    rows must carry it or they become invisible to the box that owns them."""
    rows = rows_from_sessions("real-estate", [{"id": "s-1"}], "uuid-john", "sandbox")
    assert rows[0]["instance_id"] == "sandbox"


def test_rows_from_sessions_omits_instance_id_when_absent():
    rows = rows_from_sessions("real-estate", [{"id": "s-1"}], "uuid-john")
    assert "instance_id" not in rows[0]


def test_backfill_main_stamps_instance_from_env(monkeypatch):
    monkeypatch.setenv("BACKFILL_USER_ID", "u-1")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", '{"default":"http://localhost:9119"}')
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    captured = {}

    class _Resp:
        def json(self):
            return [{"id": "s-1"}]

        def raise_for_status(self):
            return None

    monkeypatch.setattr(backfill.httpx, "get", lambda *a, **kw: _Resp())

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(backfill.httpx, "post", fake_post)
    assert backfill.main() == 0
    assert captured["json"][0]["instance_id"] == "sandbox"
