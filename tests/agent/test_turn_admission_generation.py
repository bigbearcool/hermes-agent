"""A displaced gateway worker must never become a transcript writer."""
from agent.turn_facade import TurnFacadeMixin
from tests.agent.test_cross_process_turn_lease import _DB, _agent_with_db


def test_displaced_waiter_releases_admission_without_running(monkeypatch):
    db = _DB()
    agent = _agent_with_db(db)
    current = [True]
    called = []

    def acquire(session_id, holder, **kwargs):
        db.events.append(("acquire", session_id, holder))
        # /stop releases the gateway slot while this worker is waiting. Its
        # agent is no longer reachable via that slot; mutable flags are clear.
        current[0] = False
        assert kwargs["should_abort"]() is True
        return True  # cancellation can cross a successful SQLite acquisition

    db.acquire_session_turn_lease = acquire
    monkeypatch.setattr("agent.conversation_loop.run_conversation", lambda *a, **k: called.append(True))
    result = TurnFacadeMixin.run_conversation(
        agent, "completion", turn_is_current=lambda: current[0],
    )
    assert result["interrupted"] is True
    assert result["api_calls"] == 0
    assert called == []
    assert [event[0] for event in db.events] == ["acquire", "release"]
    assert agent._active_session_turn_lease_holder is None


def test_revoked_owner_stops_renewing_even_when_interrupt_flags_are_clear():
    from agent.turn_facade_lease import DurableTurnLease
    db = _DB()
    agent = _agent_with_db(db)
    calls = []
    agent.interrupt = lambda message, **kwargs: calls.append(kwargs)
    lease = DurableTurnLease(agent, db, agent.session_id, "holder", turn_is_current=lambda: False)
    lease.turn_active = True
    assert lease.refresh_tick() is False
    assert calls == [{"hard_cancel": True, "tool_reason": "session turn lease lost"}]
    assert lease.stop.is_set()
    assert not lease.is_turn_active()


def test_generation_changes_after_admission_before_loop(monkeypatch):
    db = _DB()
    agent = _agent_with_db(db)
    current = [True]
    called = []

    def resolve_cwds(*args):
        current[0] = False
        return "", ""

    monkeypatch.setattr("agent.relay_cwd.resolve_relay_scope_cwds", resolve_cwds)
    monkeypatch.setattr("agent.conversation_loop.run_conversation", lambda *a, **k: called.append(True))
    result = TurnFacadeMixin.run_conversation(
        agent, "completion", turn_is_current=lambda: current[0],
    )
    assert result["interrupted"] is True
    assert result["api_calls"] == 0
    assert called == []
    assert [event[0] for event in db.events] == ["acquire", "release"]


def test_duplicate_lineage_waiters_cancel_without_releasing_live_holder(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent", source="feishu")
    db.end_session("parent", "compression")
    db.create_session("tip", source="feishu", parent_session_id="parent")
    assert db.try_acquire_session_turn_lease("tip", "live-owner")
    acquire = db.acquire_session_turn_lease
    called = []
    monkeypatch.setattr("agent.conversation_loop.run_conversation", lambda *a, **k: called.append(True))
    try:
        for sid in ("parent", "tip"):
            current = [True]
            agent = _agent_with_db(db, session_id=sid, platform="feishu")
            agent.clear_interrupt = lambda: called.append("cleared")

            def contended_acquire(session_id, holder, **kwargs):
                on_wait = kwargs["on_wait"]

                def stop_while_waiting(elapsed):
                    on_wait(elapsed)
                    current[0] = False
                    # Reproduce a cleared/missed interrupt after /stop.
                    agent._interrupt_requested = False

                kwargs["on_wait"] = stop_while_waiting
                return acquire(session_id, holder, poll_interval_seconds=0.01, **kwargs)

            monkeypatch.setattr(db, "acquire_session_turn_lease", contended_acquire)
            result = TurnFacadeMixin.run_conversation(
                agent, "completion", turn_is_current=lambda: current[0],
            )
            assert result["interrupted"] is True
            assert result["api_calls"] == 0
            assert not db.try_acquire_session_turn_lease(sid, "intruder")
            assert db.refresh_session_turn_lease(sid, "live-owner")
        assert called == []
    finally:
        db.release_session_turn_lease("tip", "live-owner")
        db.close()
