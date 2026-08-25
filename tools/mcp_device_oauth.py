"""Native OAuth Device Authorization for remote MCP servers.

This flow is intentionally separate from the browser-based PKCE callback
driver. It discovers the MCP protected resource and authorization server,
registers a public device client, displays the user verification URL, polls
the token endpoint at the server-provided interval, and persists the resulting
OAuth state through :class:`HermesTokenStorage`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse


DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


class DeviceOAuthError(RuntimeError):
    """A safe, step-specific Device Authorization failure."""

    def __init__(self, step: str, message: str):
        super().__init__(f"{step}: {message}")
        self.step = step


@dataclass(frozen=True)
class DeviceAuthorizationInfo:
    verification_uri: str
    verification_uri_complete: str | None
    user_code: str
    expires_in: int
    interval: int


def _require_https_url(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.netloc or (parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback)):
        raise DeviceOAuthError("metadata discovery", f"invalid {field}")
    return text


async def _read_json(response, *, step: str) -> dict:
    try:
        payload = json.loads((await response.aread()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeviceOAuthError(step, "server returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DeviceOAuthError(step, "server returned an invalid object")
    return payload


def _safe_error(payload: dict, *, fallback: str) -> str:
    code = str(payload.get("error") or "").strip()
    detail = str(payload.get("error_description") or payload.get("detail") or "").strip()
    if code and detail:
        return f"{code}: {detail[:240]}"
    return (code or detail or fallback)[:300]


async def _discover(client, server_url: str):
    from mcp.client.auth.utils import (
        build_oauth_authorization_server_metadata_discovery_urls,
        build_protected_resource_metadata_discovery_urls,
        create_oauth_metadata_request,
        handle_protected_resource_response,
    )
    from mcp.shared.auth import OAuthMetadata

    auth_server_url = None
    for url in build_protected_resource_metadata_discovery_urls(None, server_url):
        try:
            response = await client.send(create_oauth_metadata_request(url))
        except Exception:
            continue
        prm = await handle_protected_resource_response(response)
        if prm is not None:
            if prm.authorization_servers:
                auth_server_url = str(prm.authorization_servers[0])
            break

    for url in build_oauth_authorization_server_metadata_discovery_urls(
        auth_server_url, server_url
    ):
        try:
            response = await client.send(create_oauth_metadata_request(url))
        except Exception:
            continue
        if response.status_code != 200:
            continue
        raw = await _read_json(response, step="authorization server discovery")
        try:
            metadata = OAuthMetadata.model_validate(raw)
        except ValueError as exc:
            raise DeviceOAuthError(
                "authorization server discovery", "metadata is invalid"
            ) from exc
        registration_endpoint = _require_https_url(
            raw.get("registration_endpoint"), field="registration_endpoint"
        )
        device_endpoint = _require_https_url(
            raw.get("device_authorization_endpoint"),
            field="device_authorization_endpoint",
        )
        token_endpoint = _require_https_url(
            raw.get("token_endpoint"), field="token_endpoint"
        )
        grants = set(raw.get("grant_types_supported") or [])
        if DEVICE_GRANT_TYPE not in grants:
            raise DeviceOAuthError(
                "authorization server discovery", "server does not advertise Device Authorization"
            )
        return metadata, registration_endpoint, device_endpoint, token_endpoint

    raise DeviceOAuthError(
        "authorization server discovery", "no usable OAuth metadata was found"
    )


async def authorize_device(
    server_name: str,
    server_url: str,
    *,
    scope: str | None = None,
    client_name: str = "Hermes Agent",
    display: Callable[[DeviceAuthorizationInfo], None] | None = None,
    hermes_home=None,
):
    """Run one standards-based Device Authorization flow and persist it."""
    from mcp.client.auth.utils import handle_registration_response, handle_token_response_scopes
    from mcp.shared.auth import OAuthClientInformationFull
    from tools.mcp_oauth import HermesTokenStorage
    from tools.mcp_tool import sdk_httpx

    httpx = sdk_httpx()
    if httpx is None:
        raise DeviceOAuthError("client capability", "MCP SDK HTTP transport is unavailable")

    storage = HermesTokenStorage(server_name, hermes_home=hermes_home)
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        metadata, registration_endpoint, device_endpoint, token_endpoint = await _discover(
            client, server_url
        )

        registration_payload = {
            "client_name": client_name,
            "token_endpoint_auth_method": "none",
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
            "application_type": "native",
        }
        if scope:
            registration_payload["scope"] = scope
        registration_response = await client.post(
            registration_endpoint, json=registration_payload
        )
        if registration_response.status_code not in {200, 201}:
            payload = await _read_json(registration_response, step="client registration")
            raise DeviceOAuthError(
                "client registration",
                _safe_error(payload, fallback=f"HTTP {registration_response.status_code}"),
            )
        try:
            client_info = await handle_registration_response(registration_response)
        except Exception as exc:
            raise DeviceOAuthError("client registration", "invalid registration response") from exc
        client_data = client_info.model_dump(mode="json", exclude_none=True)
        client_data["issuer"] = str(metadata.issuer)
        client_info = OAuthClientInformationFull.model_validate(client_data)
        await storage.set_client_info(client_info)
        storage.save_oauth_metadata(metadata)

        device_payload = {"client_id": client_info.client_id}
        if scope:
            device_payload["scope"] = scope
        device_response = await client.post(device_endpoint, data=device_payload)
        device_data = await _read_json(device_response, step="device authorization")
        if device_response.status_code not in {200, 201}:
            raise DeviceOAuthError(
                "device authorization",
                _safe_error(device_data, fallback=f"HTTP {device_response.status_code}"),
            )
        try:
            device_code = str(device_data["device_code"])
            info = DeviceAuthorizationInfo(
                verification_uri=_require_https_url(
                    device_data["verification_uri"], field="verification_uri"
                ),
                verification_uri_complete=(
                    _require_https_url(
                        device_data["verification_uri_complete"],
                        field="verification_uri_complete",
                    )
                    if device_data.get("verification_uri_complete")
                    else None
                ),
                user_code=str(device_data["user_code"]),
                expires_in=max(1, int(device_data["expires_in"])),
                interval=max(1, int(device_data.get("interval", 5))),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceOAuthError(
                "device authorization", "response is missing required fields"
            ) from exc
        if display is not None:
            display(info)

        interval = info.interval
        deadline = asyncio.get_running_loop().time() + info.expires_in
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DeviceOAuthError("token polling", "device code expired")
            await asyncio.sleep(min(interval, remaining))
            token_response = await client.post(
                token_endpoint,
                data={
                    "grant_type": DEVICE_GRANT_TYPE,
                    "device_code": device_code,
                    "client_id": client_info.client_id,
                },
            )
            token_data = await _read_json(token_response, step="token polling")
            if 200 <= token_response.status_code < 300:
                try:
                    tokens = await handle_token_response_scopes(token_response)
                except Exception as exc:
                    raise DeviceOAuthError("token polling", "invalid token response") from exc
                await storage.set_tokens(tokens)
                return tokens
            error = str(token_data.get("error") or "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise DeviceOAuthError(
                "token polling",
                _safe_error(token_data, fallback=f"HTTP {token_response.status_code}"),
            )


def run_device_authorization(*args, **kwargs):
    """Synchronous CLI wrapper around :func:`authorize_device`."""
    return asyncio.run(authorize_device(*args, **kwargs))
