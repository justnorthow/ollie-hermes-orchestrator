from scripts.backfill_sessions import rows_from_sessions


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
