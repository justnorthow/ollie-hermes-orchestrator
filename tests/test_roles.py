"""RBAC tier model, store, and resolution (Phase 2a). Supabase I/O monkeypatched."""
import pytest
import src.api.roles as roles

INST = "sandbox"
U = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    roles.invalidate_cache()
    roles.invalidate_tags()
    yield
    roles.invalidate_cache()
    roles.invalidate_tags()


def test_tiers_and_ordering():
    assert roles.TIERS == ("member", "manager", "account_admin", "platform_operator")
    assert roles.is_at_least("account_admin", "manager") is True
    assert roles.is_at_least("member", "manager") is False
    assert roles.is_at_least("platform_operator", "platform_operator") is True


def test_resolve_tier_reads_row(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: "manager")
    assert roles.resolve_tier(INST, U) == "manager"


def test_resolve_tier_defaults_member_when_absent(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: None)
    assert roles.resolve_tier(INST, U) == "member"


def test_resolve_tier_fails_closed_on_error(monkeypatch):
    def boom(inst, uid):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(roles, "_fetch_tier", boom)
    assert roles.resolve_tier(INST, U) == "member"


def test_resolve_tier_defaults_member_on_unknown_tier(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: "superadmin")
    assert roles.resolve_tier(INST, U) == "member"


def test_resolve_tier_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: calls.append(1) or "manager")
    roles.resolve_tier(INST, U)
    roles.resolve_tier(INST, U)
    assert len(calls) == 1  # second call served from cache
    roles.invalidate_cache(U)
    roles.resolve_tier(INST, U)
    assert len(calls) == 2


def test_get_labels_merges_defaults(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_labels", lambda inst: {"manager": "Team Lead"})
    labels = roles.get_labels(INST)
    assert labels["manager"] == "Team Lead"
    assert labels["member"] == roles.DEFAULT_LABELS["member"]


def test_list_user_tags_reads_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(roles, "_fetch_tags", lambda uid: calls.append(1) or ["compliance"])
    roles.invalidate_tags()
    assert roles.list_user_tags("u-1") == ["compliance"]
    assert roles.list_user_tags("u-1") == ["compliance"]  # cached
    assert len(calls) == 1
    roles.invalidate_tags("u-1")
    roles.list_user_tags("u-1")
    assert len(calls) == 2


def test_list_user_tags_fails_closed_to_empty(monkeypatch):
    def boom(uid):
        raise RuntimeError("down")
    monkeypatch.setattr(roles, "_fetch_tags", boom)
    roles.invalidate_tags()
    assert roles.list_user_tags("u-1") == []
