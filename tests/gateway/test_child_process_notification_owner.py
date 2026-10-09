"""A child process receipt must never promote its executing agent into a chat route."""
import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import AsyncSessionStore, SessionSource, SessionStore
from hermes_state import AsyncSessionDB, SessionDB


def setup_owner(tmp_path, chat_type="group"):
    db = SessionDB(db_path=tmp_path / "state.db")
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    store._db = db
    entry = store.get_or_create_session(SessionSource(
        platform=Platform.FEISHU, chat_id="owner-chat", chat_type=chat_type, user_id="owner-user",
    ))
    db.create_session(entry.session_id, source="feishu", session_key=entry.session_key,
                      chat_id="owner-chat", user_id="owner-user")
    runner = object.__new__(GatewayRunner)
    runner._session_db = AsyncSessionDB(db)
    runner.session_store = store
    runner._async_session_store = AsyncSessionStore(store)
    return db, store, entry, runner


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "dm"])
@pytest.mark.parametrize("compressed,nested", [(False, False), (False, True), (True, False)])
async def test_child_receipt_resolves_to_gateway_owner_without_stealing_lease(tmp_path, compressed, nested, chat_type):
    db, store, entry, runner = setup_owner(tmp_path, chat_type)
    try:
        owner = entry.session_id
        if compressed:
            db.end_session(owner, end_reason="compression")
            db.create_session("owner-tip", source="feishu", parent_session_id=owner,
                              session_key=entry.session_key, chat_id="owner-chat")
            expected = "owner-tip"
        else:
            expected = owner
        db.create_session("worker", source="subagent", parent_session_id=owner,
                          model_config={"_delegate_from": owner})
        pinned = "worker"
        if nested:
            db.create_session("nested-worker", source="subagent", parent_session_id="worker")
            pinned = "nested-worker"
        assert db.acquire_session_turn_lease(pinned, "child-still-executing", wait_seconds=0)
        resolved = await runner._resolve_async_delegation_session(entry, pinned)
        assert resolved is not None and resolved.session_id == expected
        assert store.lookup_by_session_key(entry.session_key).session_id == expected
        # Real durable leases demonstrate the new chat does not wait behind the child.
        assert db.acquire_session_turn_lease(expected, "gateway-next-turn", wait_seconds=0)
        assert db.get_session(pinned)["source"] == "subagent"
        assert db.get_session(expected)["ended_at"] is None
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "dm"])
@pytest.mark.parametrize("case", ["missing-parent", "wrong-chat", "wrong-platform", "user-boundary", "cycle", "compression-child"])
async def test_child_receipt_unknown_or_unrelated_owner_preserves_current_route(tmp_path, case, chat_type):
    db, store, entry, runner = setup_owner(tmp_path, chat_type)
    try:
        parent = entry.session_id
        if case == "missing-parent":
            parent = None
        elif case == "wrong-chat":
            parent = "other-chat"
            db.create_session(parent, source="feishu", session_key="agent:main:feishu:other",
                              chat_id="different-chat")
        elif case == "wrong-platform":
            parent = "api-owner"
            db.create_session(parent, source="api_server", session_key=entry.session_key)
        elif case == "user-boundary":
            db.end_session(parent, end_reason="session_reset")
        elif case == "cycle":
            parent = "worker"
        elif case == "compression-child":
            db.end_session(parent, end_reason="compression")
        db.create_session("worker", source="subagent", parent_session_id=parent)
        resolved = await runner._resolve_async_delegation_session(entry, "worker")
        assert resolved is None
        assert store.lookup_by_session_key(entry.session_key).session_id == entry.session_id
        assert db.get_session("worker")["source"] == "subagent"
    finally:
        db.close()
