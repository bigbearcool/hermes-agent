"""Gateway ownership is captured by the executor, not a mutable agent flag."""
from types import SimpleNamespace
import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner  # bootstrap before the home-I/O guard
from gateway.run_turn_runner import TurnRunner
from gateway.session import SessionSource
from gateway.turn_context import TurnContext


def test_gateway_forwards_captured_generation_to_facade(monkeypatch):
    current = [True]
    ctx = TurnContext()
    ctx.source = SessionSource(platform=Platform.FEISHU, chat_id="test-chat")
    ctx.session_key = "agent:main:feishu:dm:test-chat"
    ctx.session_id = "test-session"
    ctx.message = "completion"
    ctx._run_still_current = lambda: current[0]
    worker = TurnRunner(SimpleNamespace(), ctx)
    worker._native_image_run_message = lambda: ctx.message
    seen = []

    class Agent:
        def run_conversation(self, message, *, turn_is_current=None, **kwargs):
            seen.append(turn_is_current)
            assert turn_is_current is ctx._run_still_current
            current[0] = False
            assert not turn_is_current()
            return {}

    monkeypatch.setattr("tools.approval.register_gateway_notify", lambda *args: None)
    monkeypatch.setattr("tools.approval.unregister_gateway_notify", lambda *args: None)
    worker._run_conversation_with_approval(Agent(), [], None, None, None)
    assert len(seen) == 1


@pytest.mark.parametrize("parent_reason, agent_sid, tip_ended, expected_dead", [
    ("compression", "tip", False, False),
    ("compression", "parent", False, True),
    ("session_reset", "tip", False, True),
    ("compression", "tip", True, True),
])
def test_compression_notification_cache_alias_is_verified(
    tmp_path, parent_reason, agent_sid, tip_ended, expected_dead,
):
    from hermes_state import SessionDB, AsyncSessionDB

    import threading

    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent", source="feishu")
    db.end_session("parent", parent_reason)
    db.create_session("tip", source="feishu", parent_session_id="parent")
    if tip_ended:
        db.end_session("tip", "session_reset")
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = AsyncSessionDB(db)
    runner.session_store = SimpleNamespace(
        _db_for_key=lambda key: db,
        _is_session_ended_in_db=lambda sid, **kw: db.get_session(sid).get("ended_at") is not None,
    )
    runner._agent_cache_lock = threading.Lock()
    key = "agent:main:feishu:dm:test-chat"
    live_agent = SimpleNamespace(session_id=agent_sid)
    runner._agent_cache = {key: (live_agent, "sig", 0, "parent")}
    ctx = TurnContext(session_key=key, session_id="tip")
    worker = TurnRunner(runner, ctx)
    try:
        # A completion pinned to the compressed parent resolves to tip while
        # the cache snapshot still names the parent. It is not a dead owner.
        peek, dead = worker._cached_sid_is_dead(runner._agent_cache_lock, runner._agent_cache)
        assert peek == "parent"
        assert dead is expected_dead
        result = worker._lookup_cached_agent("sig", runner._agent_cache_lock,
                                           runner._agent_cache, 10, peek, dead, 0)
        if expected_dead:
            assert result.agent is None
            assert result.evicted is live_agent
        else:
            assert result.agent is live_agent
            assert result.evicted is None
    finally:
        db.close()
