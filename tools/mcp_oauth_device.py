"""Explicit RFC 8628 MCP login, sharing SDK discovery, client auth and token storage.

The SDK still owns runtime requests and refresh. Device authorization is only
started by explicit MCP setup/login/reauth, never a background reconnect.
"""
from __future__ import annotations

import asyncio
import math
import sys
import time
from urllib.parse import urlparse

from mcp.shared.auth import OAuthMetadata
from pydantic import AnyHttpUrl

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class DeviceOAuthMetadata(OAuthMetadata):
    # RFC 8414 makes authorization_endpoint optional for grants not using it.
    authorization_endpoint: AnyHttpUrl | None = None
    device_authorization_endpoint: AnyHttpUrl


def _device_url(value, field, *, require_https=False):
    url = str(AnyHttpUrl(value))
    parsed = urlparse(url)
    if require_https and not (parsed.scheme == "https" or
            parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise RuntimeError(f"Device OAuth has invalid {field}")
    return url


async def _discover(client, provider, *, require_https=False):
    from mcp.client.auth.exceptions import OAuthFlowError
    from mcp.client.auth.utils import (
        build_protected_resource_metadata_discovery_urls,
        extract_resource_metadata_from_www_auth,
        handle_protected_resource_response,
    )
    context = provider.context
    response = await client.get(context.server_url)
    challenge = extract_resource_metadata_from_www_auth(response)
    prm = None
    for url in build_protected_resource_metadata_discovery_urls(challenge, context.server_url):
        response = await client.get(url)
        prm = await handle_protected_resource_response(response)
        if prm:
            await provider._validate_resource_match(prm)
            context.protected_resource_metadata = prm
            break
    # RFC 9728 lets a resource advertise several authorization servers; a browser-only
    # server often comes first and the device-code one later, so try each in order.
    servers = [str(url) for url in prm.authorization_servers] if prm else [None]
    failures = []
    for auth_server_url in servers:
        try:
            metadata = await _device_metadata(client, context.server_url, auth_server_url, require_https=require_https)
        except (RuntimeError, OAuthFlowError, ValueError) as exc:
            failures.append((auth_server_url, exc))
            continue
        context.auth_server_url = auth_server_url
        context.oauth_metadata = metadata
        return
    if len(failures) == 1:
        raise failures[0][1]
    raise RuntimeError("No advertised authorization server supports device login: "
                       + "; ".join(f"{url}: {exc}" for url, exc in failures))


async def _device_metadata(client, server_url, auth_server_url, *, require_https=False):
    """Issuer-bound device metadata of one authorization server; raises when it is unusable."""
    from mcp.client.auth.utils import build_oauth_authorization_server_metadata_discovery_urls, validate_metadata_issuer

    for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server_url, server_url):
        response = await client.get(url)
        if response.status_code == 404:
            continue
        data = _payload(response, "OAuth metadata")
        if not data.get("device_authorization_endpoint"):
            raise RuntimeError("Server does not advertise device authorization; use --flow browser if supported")
        metadata = DeviceOAuthMetadata.model_validate(data)
        if auth_server_url:
            validate_metadata_issuer(metadata, auth_server_url)
        for field in ("registration_endpoint", "device_authorization_endpoint", "token_endpoint"):
            endpoint = getattr(metadata, field, None)
            if endpoint is not None:
                _device_url(endpoint, field, require_https=require_https)
        grants = metadata.grant_types_supported
        if grants is not None and DEVICE_GRANT not in grants:
            raise RuntimeError("Server does not advertise the device_code grant")
        return metadata
    raise RuntimeError("No OAuth authorization server metadata found")


def _payload(response, label):
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"{label}: invalid JSON response") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{label}: expected a JSON object")
    if not 200 <= response.status_code < 300:
        # Descriptions and arbitrary error values may contain credentials.
        raise RuntimeError(f"{label} failed (HTTP {response.status_code})")
    return data


async def _register(client, provider, cfg):
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.client.auth.oauth2 import OAuthRegistrationError, check_registration_usable

    context = provider.context
    metadata = context.client_metadata.model_dump(mode="json", exclude_none=True)
    metadata.update(grant_types=[DEVICE_GRANT, "refresh_token"])
    # A pure device client has no browser callback. Some device-only registries
    # reject redirect/response fields rather than ignoring them.
    metadata.pop("redirect_uris", None)
    metadata.pop("response_types", None)
    if cfg.get("client_id"):
        data = {**metadata, "client_id": cfg["client_id"]}
        if cfg.get("client_secret"):
            data["client_secret"] = cfg["client_secret"]
    else:
        # Read without get_client_info's on-disk auth-method migration: a denied
        # grant must leave the previous registration and token files untouched.
        storage = context.storage
        cached = storage._load_model(storage._client_info_path(), "OAuthClientInformationFull", "client info")
        if (cached is not None and DEVICE_GRANT in (cached.grant_types or [])
                and str(getattr(cached, "issuer", "") or "").rstrip("/")
                == str(context.oauth_metadata.issuer).rstrip("/")):
            context.client_info = cached
            provider._coerce_client_secret_post()
            try:
                check_registration_usable(context.client_info)
            except OAuthRegistrationError:
                pass  # An unusable cached client needs fresh registration.
            else:
                return
        endpoint = context.oauth_metadata.registration_endpoint
        if not endpoint:
            raise RuntimeError("Server has no registration endpoint; configure oauth.client_id (and client_secret if required)")
        response = await client.post(str(endpoint), json=metadata)
        data = _payload(response, "Client registration")
    data["issuer"] = str(context.oauth_metadata.issuer)
    context.client_info = OAuthClientInformationFull.model_validate(data)
    provider._coerce_client_secret_post()
    try:
        check_registration_usable(context.client_info)
    except OAuthRegistrationError:
        raise RuntimeError("Device OAuth client has unsupported or incomplete token endpoint authentication") from None


def _positive_seconds(value, label):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"Device authorization has invalid {label}")
    return value


async def _authorize(client, provider, cfg, display=None, *, require_https=False):
    from tools.mcp_tool import sdk_httpx

    context = provider.context
    resource = context.get_resource_url()
    data = {"client_id": context.client_info.client_id, "resource": resource}
    if context.client_metadata.scope:
        data["scope"] = context.client_metadata.scope
    data, headers = context.prepare_token_auth(data, {})
    response = await client.post(str(context.oauth_metadata.device_authorization_endpoint), data=data, headers=headers)
    authorization = _payload(response, "Device authorization")
    for key in ("device_code", "user_code", "verification_uri"):
        if not isinstance(authorization.get(key), str) or not authorization[key]:
            raise RuntimeError(f"Device authorization is missing {key}")
    verification = _device_url(authorization["verification_uri"], "verification_uri", require_https=require_https)
    verification_complete = authorization.get("verification_uri_complete")
    if verification_complete:
        verification_complete = _device_url(verification_complete, "verification_uri_complete", require_https=require_https)
    interval = _positive_seconds(authorization.get("interval", 5), "interval")
    expires_in = _positive_seconds(authorization["expires_in"], "expires_in")
    deadline = time.monotonic() + min(expires_in,
                                    _positive_seconds(cfg.get("timeout", 300), "timeout"))
    if display is None:
        print(f"\n  MCP OAuth: open {verification_complete or verification} on any device.\n"
              f"  Code: {authorization['user_code']}\n  Waiting for approval...\n", file=sys.stderr, flush=True)
    else:
        display(dict(verification_uri=verification, verification_uri_complete=verification_complete,
                     user_code=authorization["user_code"], expires_in=expires_in, interval=interval))
    token_data = {"client_id": context.client_info.client_id, "device_code": authorization["device_code"],
                  "grant_type": DEVICE_GRANT, "resource": resource}
    token_data, headers = context.prepare_token_auth(token_data, {})
    httpx = sdk_httpx()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= interval:
            raise RuntimeError("Device authorization expired before approval; run login again")
        await asyncio.sleep(interval)
        request = provider._prepare_token_request(httpx.Request("POST", str(context.oauth_metadata.token_endpoint),
                                                               data=token_data, headers=headers))
        try:
            response = await asyncio.wait_for(client.send(request), timeout=deadline - time.monotonic())
        except (TimeoutError, httpx.TimeoutException):
            # RFC 8628 requires reducing polling frequency after connection timeouts.
            interval *= 2
            continue
        if 200 <= response.status_code < 300:
            from mcp.shared.auth import OAuthToken
            tokens = OAuthToken.model_validate(_payload(response, "Device token"))
            if not tokens.access_token:
                raise RuntimeError("Device token response has no access token")
            if tokens.scope is None:
                tokens.scope = context.client_metadata.scope
            return tokens
        try:
            error = response.json().get("error")
        except (ValueError, AttributeError):
            error = None
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        safe_error = error if error in {"access_denied", "expired_token"} else f"HTTP {response.status_code}"
        raise RuntimeError(f"Device authorization failed: {safe_error}")


async def login_device(name, server_url, oauth_config, *, display=None, hermes_home=None, require_https=False):
    """Authorize then commit state in the active profile; failed grants preserve old state."""
    from tools.mcp_oauth import HermesTokenStorage, _build_client_metadata
    from tools.mcp_oauth_manager import HermesMCPOAuthProvider, get_manager
    from tools.mcp_oauth_provider import prepare_oauth_config
    from tools.mcp_tool import sdk_httpx

    cfg, storage = prepare_oauth_config(name, server_url, oauth_config)
    if hermes_home is not None:
        storage = HermesTokenStorage(name, hermes_home=hermes_home)
    # Device flow never binds a callback socket or uses the hosted browser CIMD.
    cfg["_resolved_port"] = cfg.get("redirect_port", 8420)
    provider = HermesMCPOAuthProvider(server_url=server_url, server_name=name, storage=storage,
                                     client_metadata=_build_client_metadata(cfg),
                                     token_user_agent=cfg.get("user_agent"))
    httpx = sdk_httpx()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            await _discover(client, provider, require_https=require_https)
            await _register(client, provider, cfg)
            tokens = await _authorize(client, provider, cfg, display=display, require_https=require_https)
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("Device OAuth response has invalid fields") from None
    except httpx.HTTPError:
        raise RuntimeError("Device OAuth network request failed") from None
    # Validate the entire grant before touching disk; reuse the existing scoped store.
    previous = storage.snapshot()
    try:
        await storage.set_client_info(provider.context.client_info)
        storage.save_oauth_metadata(provider.context.oauth_metadata)
        storage.bind_issuer(str(provider.context.oauth_metadata.issuer))
        await storage.set_tokens(tokens)
    except OSError:
        storage.restore(previous)
        raise
    get_manager().evict(name, hermes_home=hermes_home)
    return tokens
