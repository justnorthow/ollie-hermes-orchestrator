"""Browser-facing STT/TTS endpoints (Ollie Voice v1). Engines mocked."""
import threading
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agents_json import AgentEntry, write_agent
from src.api import audio as audio_mod
from src.api.audio import router as audio_router
from src.auth import require_bearer


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    stack = tmp_path / "hermes-stack"
    stack.mkdir()
    (stack / ".env").write_text("AGENTS_JSON=[]\n")
    write_agent(stack / ".env", AgentEntry(
        id="marketing-agent", name="Olivia", gateway_port=8643,
        dashboard_port=9121, color="#888888", voice="en-US-EmmaNeural",
    ))
    app = FastAPI()
    app.state.config = types.SimpleNamespace(hermes_stack_dir=stack)
    app.include_router(audio_router)
    app.dependency_overrides[require_bearer] = lambda: None
    # fresh rate bucket per test so 429s can't leak between tests
    monkeypatch.setattr(audio_mod, "_bucket", audio_mod.TokenBucket(rate_per_min=1000))
    return TestClient(app), monkeypatch


AUTH = {"X-Auth-User-Id": "u1"}


def _mock_synth(monkeypatch, out=b"MP3BYTES"):
    calls = []
    async def fake(text, voice):
        calls.append((text, voice))
        return out
    monkeypatch.setattr(audio_mod, "_synthesize", fake)
    return calls


def test_speak_requires_signed_in_user(ctx):
    c, _ = ctx
    r = c.post("/v1/audio/speak", json={"text": "hi", "agentId": "marketing-agent"})
    assert r.status_code == 401


def test_speak_returns_mpeg_with_agent_voice(ctx):
    c, monkeypatch = ctx
    calls = _mock_synth(monkeypatch)
    r = c.post("/v1/audio/speak", json={"text": "hello there", "agentId": "marketing-agent"}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content == b"MP3BYTES"
    assert calls == [("hello there", "en-US-EmmaNeural")]


def test_speak_unknown_agent_falls_back_to_env_then_default(ctx):
    c, monkeypatch = ctx
    calls = _mock_synth(monkeypatch)
    monkeypatch.setenv("TTS_DEFAULT_VOICE", "en-AU-WilliamNeural")
    assert c.post("/v1/audio/speak", json={"text": "x", "agentId": "nope"}, headers=AUTH).status_code == 200
    monkeypatch.delenv("TTS_DEFAULT_VOICE")
    assert c.post("/v1/audio/speak", json={"text": "y", "agentId": "nope"}, headers=AUTH).status_code == 200
    assert calls[0][1] == "en-AU-WilliamNeural"
    assert calls[1][1] == audio_mod._FALLBACK_VOICE


def test_speak_caps(ctx):
    c, monkeypatch = ctx
    _mock_synth(monkeypatch)
    assert c.post("/v1/audio/speak", json={"text": "  ", "agentId": "a"}, headers=AUTH).status_code == 400
    assert c.post("/v1/audio/speak", json={"text": "x" * 5001, "agentId": "a"}, headers=AUTH).status_code == 413


def test_speak_engine_failure_is_502(ctx):
    c, monkeypatch = ctx
    async def boom(text, voice):
        raise RuntimeError("edge down")
    monkeypatch.setattr(audio_mod, "_synthesize", boom)
    r = c.post("/v1/audio/speak", json={"text": "hi", "agentId": "marketing-agent"}, headers=AUTH)
    assert r.status_code == 502


def test_speak_rate_limited_is_429(ctx):
    c, monkeypatch = ctx
    _mock_synth(monkeypatch)
    monkeypatch.setattr(audio_mod, "_bucket", audio_mod.TokenBucket(rate_per_min=1))
    assert c.post("/v1/audio/speak", json={"text": "a", "agentId": "x"}, headers=AUTH).status_code == 200
    assert c.post("/v1/audio/speak", json={"text": "b", "agentId": "x"}, headers=AUTH).status_code == 429


class _FakeSeg:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    def __init__(self, segs=("hello", "world")):
        self.calls = 0
        self._segs = segs

    def transcribe(self, f):
        self.calls += 1
        return [_FakeSeg(f" {s} ") for s in self._segs], {"language": "en"}


def test_transcribe_requires_signed_in_user(ctx):
    c, _ = ctx
    assert c.post("/v1/audio/transcribe", content=b"xx").status_code == 401


def test_transcribe_joins_segments(ctx):
    c, monkeypatch = ctx
    model = _FakeModel()
    monkeypatch.setattr(audio_mod, "_get_model", lambda: model)
    r = c.post("/v1/audio/transcribe", content=b"FAKEWEBM", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"text": "hello world"}
    assert model.calls == 1


def test_transcribe_caps(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(audio_mod, "_get_model", lambda: _FakeModel())
    assert c.post("/v1/audio/transcribe", content=b"", headers=AUTH).status_code == 400
    big = b"x" * (audio_mod._MAX_AUDIO_BYTES + 1)
    assert c.post("/v1/audio/transcribe", content=big, headers=AUTH).status_code == 413


def test_transcribe_decode_failure_is_400(ctx):
    c, monkeypatch = ctx
    class _Broken:
        def transcribe(self, f):
            raise ValueError("not audio")
    monkeypatch.setattr(audio_mod, "_get_model", lambda: _Broken())
    r = c.post("/v1/audio/transcribe", content=b"not-audio", headers=AUTH)
    assert r.status_code == 400


def test_transcribe_model_load_failure_is_502(ctx):
    c, monkeypatch = ctx
    def boom():
        raise RuntimeError("no ctranslate2")
    monkeypatch.setattr(audio_mod, "_get_model", boom)
    assert c.post("/v1/audio/transcribe", content=b"xx", headers=AUTH).status_code == 502


def test_transcribe_silence_returns_empty_text(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(audio_mod, "_get_model", lambda: _FakeModel(segs=()))
    r = c.post("/v1/audio/transcribe", content=b"quiet", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"text": ""}


def test_transcribe_timeout_is_504(ctx):
    # The whisper thread can't be cancelled once started (asyncio.to_thread
    # has no interrupt hook) — asyncio.wait_for just stops us waiting on it.
    # Shrink the timeout via the extracted constant so a fake model that
    # "hangs" past it trips the 504 deterministically and fast.
    c, monkeypatch = ctx
    monkeypatch.setattr(audio_mod, "_TRANSCRIBE_TIMEOUT_S", 0.05)

    class _SlowModel:
        def transcribe(self, f):
            time.sleep(0.5)
            return [_FakeSeg("late")], {"language": "en"}

    monkeypatch.setattr(audio_mod, "_get_model", lambda: _SlowModel())
    r = c.post("/v1/audio/transcribe", content=b"xx", headers=AUTH)
    assert r.status_code == 504


def test_transcribe_serializes_concurrent_requests(ctx):
    # _transcribe_gate (asyncio.Semaphore(1)) must keep a second transcription
    # from starting until the first finishes, even under real concurrency.
    # Use `with c:` so both requests run on a single shared event loop/portal
    # (the module-level semaphore isn't meaningfully shared across the
    # separate portals TestClient spins up per call otherwise).
    c, monkeypatch = ctx
    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}
    started = threading.Event()
    release = threading.Event()

    class _GatedModel:
        def transcribe(self, f):
            with lock:
                state["in_flight"] += 1
                state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            started.set()
            release.wait(timeout=5)
            with lock:
                state["in_flight"] -= 1
            return [_FakeSeg("ok")], {"language": "en"}

    monkeypatch.setattr(audio_mod, "_get_model", lambda: _GatedModel())

    results = []

    def worker():
        r = c.post("/v1/audio/transcribe", content=b"xx", headers=AUTH)
        results.append(r.status_code)

    with c:
        t1 = threading.Thread(target=worker)
        t1.start()
        assert started.wait(timeout=5)
        started.clear()

        t2 = threading.Thread(target=worker)
        t2.start()
        # Give the second request a moment to (wrongly) sneak past the gate;
        # it must still be waiting, never having reached transcribe().
        time.sleep(0.3)
        assert not started.is_set()
        with lock:
            assert state["max_in_flight"] == 1

        release.set()
        t1.join(timeout=5)
        assert started.wait(timeout=5)  # second request now proceeds
        t2.join(timeout=5)

    assert results == [200, 200]
    assert state["max_in_flight"] == 1
