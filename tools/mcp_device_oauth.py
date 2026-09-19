"""Compatibility API for the shared MCP Device Authorization flow.

The CLI add command keeps its approval display and callers keep the original
async/sync entrypoints; protocol validation and persistence belong to
``tools.mcp_oauth_device.login_device``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable


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


async def authorize_device(
    server_name: str,
    server_url: str,
    *,
    scope: str | None = None,
    client_name: str = "Hermes Agent",
    display: Callable[[DeviceAuthorizationInfo], None] | None = None,
    hermes_home=None,
):
    """Authorize through the shared flow, preserving this API's display contract."""
    try:
        from tools.mcp_oauth_device import login_device
    except ImportError as exc:
        raise DeviceOAuthError("client capability", "MCP SDK OAuth support is unavailable") from exc

    def show(info):
        if display is not None:
            display(DeviceAuthorizationInfo(
                **{**info, "expires_in": max(1, int(info["expires_in"])),
                   "interval": max(1, int(info["interval"]))},
            ))

    try:
        return await login_device(
            server_name, server_url,
            {"flow": "device", "scope": scope, "client_name": client_name},
            display=show, hermes_home=hermes_home, require_https=True,
        )
    except RuntimeError as exc:
        raise DeviceOAuthError("device authorization", str(exc)) from exc


def run_device_authorization(*args, **kwargs):
    """Synchronous CLI wrapper around :func:`authorize_device`."""
    return asyncio.run(authorize_device(*args, **kwargs))
