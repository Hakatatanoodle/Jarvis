"""Unit tests for infra/mcp_client.py. Only _parse_tool_result and the
two small helpers it calls are pure/no-I/O, so those are what's tested
here against fake result objects — the actual session/transport code in
call_calendar_tool is exercised indirectly wherever it's mocked out
(tests/test_m5_capabilities.py's calendar tests), not re-tested here.
No real network path to a calendar-mcp server exists in this sandbox
(see infra/mcp_client.py's own module docstring)."""
from types import SimpleNamespace

import pytest

from infra.mcp_client import CalendarAuthError, CalendarMCPError, _parse_tool_result


def _fake_result(text: str, is_error: bool = False):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        isError=is_error,
    )


def test_parses_json_object_result():
    result = _fake_result('{"events": [{"id": "evt-1"}]}')
    parsed = _parse_tool_result("list-events", result)
    assert parsed == {"events": [{"id": "evt-1"}]}


def test_parses_json_array_result_wrapped_in_result_key():
    result = _fake_result('[{"id": "evt-1"}, {"id": "evt-2"}]')
    parsed = _parse_tool_result("list-events", result)
    assert parsed == {"result": [{"id": "evt-1"}, {"id": "evt-2"}]}


def test_non_json_text_result_wrapped_in_text_key():
    result = _fake_result("2026-08-30T12:00:00Z")
    parsed = _parse_tool_result("get-current-time", result)
    assert parsed == {"text": "2026-08-30T12:00:00Z"}


def test_empty_result_is_empty_dict():
    result = SimpleNamespace(content=[], isError=False)
    assert _parse_tool_result("create-event", result) == {}


def test_auth_looking_error_raises_calendar_auth_error():
    result = _fake_result("Error: token expired, please re-authenticate", is_error=True)
    with pytest.raises(CalendarAuthError) as excinfo:
        _parse_tool_result("create-event", result)
    # The whole point is a readable, actionable message, not a bare
    # protocol error passed straight through.
    assert "authenticate" in str(excinfo.value).lower()
    assert "token expired" in str(excinfo.value)


def test_dash_32600_code_treated_as_auth_error():
    # Upstream's own README says an unauthenticated call returns this.
    result = _fake_result("-32600: invalid request, no account authenticated", is_error=True)
    with pytest.raises(CalendarAuthError):
        _parse_tool_result("list-events", result)


def test_non_auth_error_raises_plain_calendar_mcp_error():
    result = _fake_result("calendar not found: bad-calendar-id@group.calendar.google.com", is_error=True)
    with pytest.raises(CalendarMCPError) as excinfo:
        _parse_tool_result("create-event", result)
    assert not isinstance(excinfo.value, CalendarAuthError)
    assert "calendar not found" in str(excinfo.value)


def test_error_with_no_text_content_still_raises_with_placeholder():
    result = SimpleNamespace(content=[], isError=True)
    with pytest.raises(CalendarMCPError) as excinfo:
        _parse_tool_result("create-event", result)
    assert "no detail returned" in str(excinfo.value)
