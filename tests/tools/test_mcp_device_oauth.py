"""Tests for the native MCP OAuth Device Authorization flow."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_device_flow_discovers_registers_polls_and_persists(tmp_path, monkeypatch):
    from tools import mcp_device_oauth
    from tools.mcp_tool import sdk_httpx

    real_httpx = sdk_httpx()
    assert real_httpx is not None
    requests = []

    def response(status, payload, url):
        return real_httpx.Response(
            status,
            json=payload,
            request=real_httpx.Request("POST", url),
        )

    class FakeClient:
        def __init__(self, **_kwargs):
            self.poll_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, request):
            url = str(request.url)
            requests.append(("GET", url, None))
            if "oauth-protected-resource" in url:
                return response(
                    200,
                    {
                        "resource": "https://tenant.example.com/mcp",
                        "authorization_servers": ["https://tenant.example.com"],
                    },
                    url,
                )
            return response(
                200,
                {
                    "issuer": "https://tenant.example.com",
                    "authorization_endpoint": "https://tenant.example.com/oauth/authorize",
                    "device_authorization_endpoint": "https://tenant.example.com/oauth/device/authorization",
                    "token_endpoint": "https://tenant.example.com/oauth/token",
                    "registration_endpoint": "https://tenant.example.com/oauth/register",
                    "grant_types_supported": [
                        mcp_device_oauth.DEVICE_GRANT_TYPE,
                        "refresh_token",
                    ],
                    "response_types_supported": ["code"],
                },
                url,
            )

        async def post(self, url, *, json=None, data=None):
            requests.append(("POST", url, json if json is not None else data))
            if url.endswith("/oauth/register"):
                return response(
                    201,
                    {
                        "client_id": "device-client-id",
                        "client_name": "Hermes Agent",
                        "redirect_uris": [],
                        "token_endpoint_auth_method": "none",
                        "grant_types": [
                            mcp_device_oauth.DEVICE_GRANT_TYPE,
                            "refresh_token",
                        ],
                        "response_types": [],
                        "scope": "agent:list work:read",
                        "application_type": "native",
                    },
                    url,
                )
            if url.endswith("/oauth/device/authorization"):
                return response(
                    201,
                    {
                        "device_code": "secret-device-code",
                        "user_code": "XS-ABCD-2345",
                        "verification_uri": "https://tenant.example.com/settings/personal-agent",
                        "verification_uri_complete": "https://tenant.example.com/settings/personal-agent?code=XS-ABCD-2345",
                        "expires_in": 600,
                        "interval": 1,
                    },
                    url,
                )
            self.poll_count += 1
            if self.poll_count == 1:
                return response(400, {"error": "authorization_pending"}, url)
            return response(
                200,
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "agent:list work:read",
                },
                url,
            )

    class FakeHttpx:
        Timeout = real_httpx.Timeout
        AsyncClient = FakeClient

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("tools.mcp_tool.sdk_httpx", lambda: FakeHttpx)
    monkeypatch.setattr(mcp_device_oauth.asyncio, "sleep", no_sleep)
    displayed = []

    tokens = await mcp_device_oauth.authorize_device(
        "xiaosheng",
        "https://tenant.example.com/mcp",
        scope="agent:list work:read",
        display=displayed.append,
        hermes_home=tmp_path,
    )

    assert tokens.access_token == "access-secret"
    assert displayed[0].user_code == "XS-ABCD-2345"
    registration = next(payload for method, url, payload in requests if url.endswith("/oauth/register"))
    assert registration["grant_types"] == [mcp_device_oauth.DEVICE_GRANT_TYPE, "refresh_token"]
    assert "redirect_uris" not in registration
    assert "response_types" not in registration
    device_request = next(
        payload for method, url, payload in requests if url.endswith("/oauth/device/authorization")
    )
    assert device_request["client_id"] == "device-client-id"
    token_file = tmp_path / "mcp-tokens" / "xiaosheng.json"
    client_file = tmp_path / "mcp-tokens" / "xiaosheng.client.json"
    assert json.loads(token_file.read_text())["refresh_token"] == "refresh-secret"
    assert json.loads(client_file.read_text())["client_id"] == "device-client-id"
    assert token_file.stat().st_mode & 0o077 == 0


def test_device_cli_parser_accepts_scope_and_device_auth():
    import argparse

    from hermes_cli.subcommands.mcp import build_mcp_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_mcp_parser(subparsers, cmd_mcp=lambda _args: None)

    args = parser.parse_args(
        [
            "mcp",
            "add",
            "xiaosheng",
            "--url",
            "https://tenant.example.com/mcp",
            "--auth",
            "device",
            "--scope",
            "agent:list work:read",
        ]
    )

    assert args.auth == "device"
    assert args.scope == "agent:list work:read"
