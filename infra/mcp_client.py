"""M5: minimal async MCP client, scoped to the calendar-mcp server
(nspady/google-calendar-mcp, run locally per docker-compose.yml's
`calendar-mcp` service). Not a general-purpose MCP client library —
Jarvis has exactly one MCP server today, and building a generic
multi-server abstraction with only one real caller to generalize from
would repeat the same mistake M4's own risk_calculator.py docstring
warns against (don't generalize ahead of a second data point).

Connects fresh per tool call rather than holding a long-lived
ClientSession open across calls. Simplest-correct-first (same principle
the architect gave for M3, cited in permission/risk_calculator.py) —
session pooling/reuse is a real future optimization once there's
traffic to justify it, not a correctness requirement now.

Requires local verification: this sandbox has no network path to a
running calendar-mcp server (Docker Hub / arbitrary registries aren't
in the network allowlist — see docker-compose.yml's own comment) or to
Google's OAuth endpoints, so nothing here has been exercised against a
live server. Two specific things that need confirming once it can be:
1. The `mcp` PyPI package's client transport import path — verified
   against `mcp==2.1.1` (latest on PyPI as of this pin) as
   `mcp.client.streamable_http.streamable_http_client`, but this name
   has moved across SDK versions before (the older, still widely
   documented spelling is `streamablehttp_client`); the try/except
   import below covers both, but neither has been run.
2. The exact shape of an auth-failure response from calendar-mcp
   (`_looks_like_auth_error` below is a keyword heuristic built from
   upstream's docs — "auth", "token", "expired", "unauthorized",
   "invalid_grant" (Google OAuth's own code for a dead refresh token),
   and the "-32600 error" upstream's README says an unauthenticated
   call returns — not from an observed real response).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from config.loader import load_config
from infra.logging import get_logger

log = get_logger("infra.mcp_client")

try:
    from mcp.client.streamable_http import streamable_http_client as _http_client
except ImportError:  # pragma: no cover - older/newer SDK spelling
    from mcp.client.streamable_http import streamablehttp_client as _http_client
from mcp import ClientSession


class CalendarMCPError(Exception):
    """A calendar-mcp tool call failed for a reason other than auth.
    Deliberately a plain Exception (not a richer contract type) — this
    is an infra-layer error, surfaced to the user via whatever caught
    it and turned it into `CapabilityResult.error` (action_engine.api's
    run_action does this for any exception any executor raises)."""


class CalendarAuthError(CalendarMCPError):
    """Raised when a tool call fails for what looks like an auth/token
    reason. Carries a human-readable, actionable message (not a raw
    protocol error) — this is the honest-failure-message requirement
    the M5 handover called out by name (a stale/expired token must not
    surface as a confusing generic error)."""


_AUTH_ERROR_KEYWORDS = (
    "auth", "token", "expired", "unauthorized", "unauthenticated",
    "invalid_grant", "-32600", "re-auth", "not authenticated",
)


def _looks_like_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(kw in lowered for kw in _AUTH_ERROR_KEYWORDS)


def _auth_error_message(detail: str) -> str:
    return (
        "Google Calendar needs to be (re)authenticated before this can "
        "run — visit http://127.0.0.1:3000/accounts to connect or "
        "re-authenticate an account (or run `docker compose exec "
        f"calendar-mcp npm run auth`). Underlying detail: {detail}"
    )


def _parse_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
    """Pure — no I/O. Split out from call_calendar_tool() specifically so
    tests can exercise the parsing/error-classification logic (the part
    with actual decisions in it) against simple fake result objects,
    without mocking the transport/session machinery to get there. See
    tests/test_mcp_client.py.
    """
    text_parts = [
        block.text for block in getattr(result, "content", []) or []
        if getattr(block, "type", None) == "text"
    ]
    raw_text = "\n".join(text_parts)

    if getattr(result, "isError", False):
        if _looks_like_auth_error(raw_text):
            raise CalendarAuthError(_auth_error_message(raw_text or "no detail returned"))
        raise CalendarMCPError(f"calendar-mcp tool '{tool_name}' failed: {raw_text or 'no detail returned'}")

    if not raw_text:
        return {}
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Some tools (e.g. get-current-time) may return plain text, not
        # JSON — wrap it rather than fail, callers that expect structure
        # will get a clear KeyError instead of a silent None.
        return {"text": raw_text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _unwrap_exception_group(exc: BaseException) -> BaseException:
    """Python's TaskGroup (used internally by the mcp SDK's streamable-
    HTTP transport for its background read/write tasks) wraps any
    failure in an ExceptionGroup, whose default str() is the useless
    "unhandled errors in a TaskGroup (1 sub-exception)" — confirmed
    directly against a real Jarvis session hitting this (2026-09-01).
    Drills down to the first actual leaf exception so callers/logs see
    the real cause (connection refused, a protocol error, etc.) instead
    of the wrapper. Multi-sub-exception groups are rare for a single
    request/response round trip; taking the first is a reasonable
    default, not a guarantee every sibling failure is captured — if
    this turns out to hide something, log.exception's traceback (not
    added here to keep this a pure function) is the fallback source of
    truth.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


async def call_calendar_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Opens a fresh MCP session to the configured calendar-mcp server,
    calls one tool, and returns its result parsed as a dict (via
    _parse_tool_result above).

    Raises CalendarAuthError for anything that looks like an auth/token
    problem, CalendarMCPError for any other tool-level failure, and lets
    connection-level exceptions (the server isn't running, wrong URL,
    etc.) propagate as-is — those are infrastructure problems, not
    calendar-domain ones, and dressing them up here would hide the real
    cause from whoever's debugging a down container. Connection-level
    failures are unwrapped from any TaskGroup ExceptionGroup first (see
    _unwrap_exception_group above) so "propagate as-is" actually means
    something a person can read, not a generic wrapper.
    """
    config = load_config()
    server_url = config.get("calendar.mcp.server_url")
    timeout = config.get("calendar.mcp.request_timeout_seconds", 15)
    if not server_url:
        raise CalendarMCPError("calendar.mcp.server_url is not configured (config/default.yaml)")

    log.info(f"Calling calendar-mcp tool '{tool_name}'")
    try:
        async with _http_client(server_url) as (read, write, *_rest):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments, read_timeout_seconds=timeout)
    except* Exception as eg:
        real = _unwrap_exception_group(eg)
        raise CalendarMCPError(f"calendar-mcp connection/session failed ({type(real).__name__}): {real}") from real

    return _parse_tool_result(tool_name, result)
