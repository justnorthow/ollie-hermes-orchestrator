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
