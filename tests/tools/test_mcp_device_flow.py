"""RFC 8628 login invariants through the production CLI and local HTTP."""
from pathlib import Path

import pytest

pytest.importorskip(
    "mcp.client.auth.oauth2",
    reason="MCP SDK 1.26.0+ required for device-flow coverage",
)

from evals.mcp_device_flow import DEVICE_GRANT, oauth_fixture, run_cli


@pytest.mark.parametrize("mode", ["success", "preregistered", "multi_issuer"])
def test_device_login_registers_authorizes_and_persists(mode):
    result = run_cli(Path(__file__).resolve().parents[2], mode)
    assert result["token_persisted"], result
    assert "TEST-CODE" in result["output"]
    assert "Authenticated" in result["output"]
    tokens = [row for row in result["wire"] if row["path"] == "/token"]
    polls = [row for row in tokens if row["data"]["grant_type"] != "refresh_token"]
    assert any(row["data"]["grant_type"] == "refresh_token" for row in tokens), result
    assert "Connected" in result["refresh_output"], result
    assert all(row["data"]["grant_type"] == DEVICE_GRANT for row in polls)
    assert all(row["data"]["resource"].endswith("/mcp") for row in polls)
    if mode == "success":
        assert polls[2]["at"] - polls[1]["at"] >= 5
    elif mode == "preregistered":
        assert not any(row["path"] == "/register" for row in result["wire"])


@pytest.mark.parametrize("mode", ["denied", "expiry", "unsupported", "issuer", "resource", "malformed", "persistence"])
def test_device_login_failure_does_not_persist_or_disclose_credentials(mode):
    result = run_cli(Path(__file__).resolve().parents[2], mode)
    assert "unrecognized arguments" not in result["output"], result
    assert result["state_preserved"], result
    assert result["token_persisted"] == (mode == "persistence"), result
    assert "Authentication failed" in result["output"], result
    assert "fixture-device-secret" not in result["output"], result
    assert "Authenticated" not in result["output"], result


@pytest.mark.parametrize("mode", ["success", "denied", "issuer", "resource", "persistence"])
def test_device_add_shares_validated_grant_and_persistence(mode):
    result = run_cli(Path(__file__).resolve().parents[2], mode, entrypoint="add")
    assert result["state_preserved"], result
    assert "fixture-device-secret" not in result["output"], result
    assert result["token_persisted"] == (mode in {"success", "persistence"}), result
    if mode == "success":
        assert "Device Authorization completed" in result["output"], result
        assert "Connected" in result["refresh_output"], result
        registration = next(row["data"] for row in result["wire"] if row["path"] == "/register")
        assert "redirect_uris" not in registration and "response_types" not in registration
        polls = [row["data"] for row in result["wire"] if row["path"] == "/token"
                 and row["data"]["grant_type"] == DEVICE_GRANT]
        assert polls and all(data["resource"].endswith("/mcp") for data in polls)
    else:
        assert "Device Authorization failed" in result["output"], result
        assert "Device Authorization completed" not in result["output"], result


@pytest.mark.asyncio
async def test_explicit_device_home_keeps_tokens_and_cache_isolated_a_b_a(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.mcp_device_oauth import authorize_device
    from tools.mcp_oauth import HermesTokenStorage
    from tools.mcp_oauth_manager import get_manager, reset_manager_for_tests

    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(launch))
    homes = {key: tmp_path / key for key in ("a", "b")}
    for home in homes.values():
        home.mkdir()
    reset_manager_for_tests()
    manager = get_manager()
    stores = {key: HermesTokenStorage("fixture", hermes_home=home) for key, home in homes.items()}
    snapshots, cached = {}, {}
    try:
        with oauth_fixture("profile") as (url_a, wire_a), oauth_fixture("profile") as (url_b, wire_b):
            urls = {"a": url_a, "b": url_b}
            for key in ("a", "b", "a"):
                other = "b" if key == "a" else "a"
                # The explicit target must win over a different active profile.
                token = set_hermes_home_override(homes[other])
                try:
                    tokens = await authorize_device("fixture", urls[key] + "/mcp", hermes_home=homes[key])
                    assert (await stores[key].get_tokens()).access_token == tokens.access_token
                    assert stores[key].loaded_issuer.rstrip("/") == urls[key]
                    assert manager._key("fixture", homes[key]) not in manager._entries
                    if other in snapshots:
                        assert stores[other].snapshot() == snapshots[other]
                        assert manager._entries[manager._key("fixture", homes[other])].provider is cached[other]
                finally:
                    reset_hermes_home_override(token)

                token = set_hermes_home_override(homes[key])
                try:
                    cached[key] = manager.get_or_build_provider(
                        "fixture", urls[key] + "/mcp", {"flow": "device", "cimd": False},
                    )
                    assert cached[key] is not None
                finally:
                    reset_hermes_home_override(token)
                snapshots[key] = stores[key].snapshot()
            assert sum(row["path"] == "/register" for row in wire_a) == 1
            assert sum(row["path"] == "/register" for row in wire_b) == 1
        assert not (launch / "mcp-tokens").exists()
    finally:
        reset_manager_for_tests()
