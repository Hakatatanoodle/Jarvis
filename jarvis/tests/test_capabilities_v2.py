"""Capabilities V2: web.search + reminders (create/list/cancel + delivery).
The search API is mocked at httpx.AsyncClient.post — no real network."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import capabilities.bootstrap  # noqa: F401
from action_engine.api import create_action, run_action
from capabilities.registry import get as get_capability
from capability_invocation.dispatch import resolve_and_dispatch
from contracts.action import Action
from contracts.capability_invocation_candidate import CapabilityInvocationCandidate
from contracts.enums import ActionType, PermissionStatus, RiskLevel
from mission.api import set_mission
from permission.api import check_permission
from permission.risk_calculator import compute_risk
from reminders import api as rapi

pytestmark = pytest.mark.usefixtures("clean_db")

_REQ = httpx.Request("POST", "https://api.tavily.com/search")


def _resp(status=200, json=None):
    return httpx.Response(status, json=json if json is not None else {}, request=_REQ)


def _future(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


# ---- risk math (pins the arithmetic in each module docstring) ----

@pytest.mark.parametrize("cap_id,atype,expected,status", [
    ("web.search", ActionType.READ, RiskLevel.LOW, PermissionStatus.GRANTED),
    ("reminders.create", ActionType.WRITE, RiskLevel.MEDIUM, PermissionStatus.GRANTED),
    ("reminders.list", ActionType.READ, RiskLevel.LOW, PermissionStatus.GRANTED),
    ("reminders.cancel", ActionType.WRITE, RiskLevel.HIGH, PermissionStatus.CONFIRMATION_REQUIRED),
])
async def test_risk_levels(cap_id, atype, expected, status):
    cap, _ = get_capability(cap_id)
    action = Action(plan_id="p", capability_id=cap_id, type=atype, title="t", parameters={})
    computed, _f = compute_risk(cap, action)
    assert computed == expected
    assert not cap.force_confirmation
    # Permission mapping: LOW/MEDIUM granted, HIGH confirm (never CRITICAL/DENIED)
    assert (PermissionStatus.GRANTED if computed in (RiskLevel.LOW, RiskLevel.MEDIUM) else PermissionStatus.CONFIRMATION_REQUIRED) == status


async def _plan_id():
    from goals.api import create_goal
    from planner.api import create_plan_from_decision
    from reasoning.api import reason
    from contracts.enums import GoalType
    await set_mission(title="Grow", statement="S.")
    await create_goal(type=GoalType.LIFE_GOAL, title="seed")
    return (await create_plan_from_decision(await reason(intent="Planning", objective="seed"))).id


async def test_permission_check_real_path_web_and_reminder():
    pid = await _plan_id()
    for cid, at, params in [("web.search", ActionType.READ, {"query": "x"}),
                            ("reminders.create", ActionType.WRITE, {"reminder_text": "x", "due_at": _future(hours=1)})]:
        a = await create_action(plan_id=pid, capability_id=cid, type=at, title="t", parameters=params)
        cap, _ = get_capability(cid)
        assert (await check_permission(a.id, cap)).permission_status == PermissionStatus.GRANTED


# ---- web.search ----

@pytest.fixture
def tavily_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")


async def test_search_bounded_clipped_and_authenticated(tavily_key):
    raw = {"results": [{"title": "T" * 500, "url": f"https://e.com/{i}", "content": "c" * 900} for i in range(10)]
           + [{"title": "no url", "content": "x"}]}
    _cap, ex = get_capability("web.search")
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(json=raw))) as post:
        out = await ex({"query": " nepal weather "})
    assert out["query"] == "nepal weather"
    assert len(out["results"]) == 6
    assert len(out["results"][0]["snippet"]) == 300 and len(out["results"][0]["title"]) == 150
    assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer tvly-test"
    assert post.await_args.kwargs["json"]["query"] == "nepal weather"


async def test_search_missing_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    _c, ex = get_capability("web.search")
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        await ex({"query": "x"})


@pytest.mark.parametrize("side,match", [
    (httpx.ReadTimeout("t"), "timed out"),
    (httpx.ConnectError("c"), "couldn't connect"),
])
async def test_search_network_errors(tavily_key, side, match):
    _c, ex = get_capability("web.search")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=side)):
        with pytest.raises(RuntimeError, match=match):
            await ex({"query": "x"})


@pytest.mark.parametrize("status,match", [(401, "rejected"), (429, "quota"), (500, "HTTP 500")])
async def test_search_http_errors(tavily_key, status, match):
    _c, ex = get_capability("web.search")
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(status))):
        with pytest.raises(RuntimeError, match=match):
            await ex({"query": "x"})


async def test_search_failure_becomes_capability_result_not_crash(tavily_key):
    pid = await _plan_id()
    a = await create_action(plan_id=pid, capability_id="web.search", type=ActionType.READ, title="t", parameters={"query": "x"})
    cap, _ = get_capability("web.search")
    await check_permission(a.id, cap)
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ReadTimeout("t"))):
        res = await run_action(a.id)
    assert res.success is False and "timed out" in res.error


async def test_dispatch_search_grounds_reply_in_results(tavily_key):
    await set_mission(title="Grow", statement="S.")
    cand = CapabilityInvocationCandidate(capability_id="web.search", raw_user_text="search the web for foo", extra={"query": "foo"})
    raw = {"results": [{"title": "Foo Page", "url": "https://foo.com", "content": "Foo is a thing."}]}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(json=raw))):
        executed, relay, pending = await resolve_and_dispatch(cand)
    assert relay is None and pending == []
    assert "Foo Page" in executed and "https://foo.com" in executed and "Foo is a thing." in executed


async def test_dispatch_search_without_query_asks_clarify():
    cand = CapabilityInvocationCandidate(capability_id="web.search", raw_user_text="search the web")
    executed, relay, pending = await resolve_and_dispatch(cand)
    assert executed is None and "search the web for" in relay.lower()


# ---- reminders: repository + delivery ----

async def test_pop_due_returns_only_due_exactly_once():
    now = datetime.now(timezone.utc)
    await rapi.create_reminder("past", now - timedelta(minutes=5))
    await rapi.create_reminder("future", now + timedelta(hours=1))
    first, second = await asyncio.gather(rapi.pop_due_reminders(), rapi.pop_due_reminders())
    delivered = first + second
    assert [r.text for r in delivered] == ["past"]  # concurrent polls: exactly one gets it
    assert await rapi.pop_due_reminders() == []
    assert [r.text for r in await rapi.list_reminders()] == ["future"]


async def test_cancelled_reminder_never_delivered():
    r = await rapi.create_reminder("nope", datetime.now(timezone.utc) + timedelta(seconds=1))
    assert await rapi.cancel_reminder(r.id) is True
    assert await rapi.cancel_reminder(r.id) is False
    assert await rapi.pop_due_reminders(now=datetime.now(timezone.utc) + timedelta(hours=1)) == []


async def test_due_endpoint_returns_json_and_marks_delivered():
    from server.app import reminders_due
    await rapi.create_reminder("ping", datetime.now(timezone.utc) - timedelta(seconds=5))
    out = await reminders_due()
    assert [r["text"] for r in out] == ["ping"] and "due_at" in out[0]
    assert await reminders_due() == []


# ---- reminders: executors ----

async def test_create_executor_stores_utc_and_rejects_past():
    _c, ex = get_capability("reminders.create")
    out = await ex({"reminder_text": "call mom", "due_at": _future(hours=2)})
    assert out["text"] == "call mom" and len(await rapi.list_reminders()) == 1
    with pytest.raises(ValueError, match="already passed"):
        await ex({"reminder_text": "old", "due_at": _future(hours=-2)})


async def test_create_executor_naive_datetime_uses_user_timezone():
    _c, ex = get_capability("reminders.create")
    local = (datetime.now(timezone.utc) + timedelta(days=1)).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Kathmandu")).replace(tzinfo=None, microsecond=0)
    out = await ex({"reminder_text": "x", "due_at": local.isoformat()})
    stored = datetime.fromisoformat(out["due_at"])
    assert stored.utcoffset() == timedelta(0)
    assert abs((stored - (local - timedelta(hours=5, minutes=45)).replace(tzinfo=timezone.utc)).total_seconds()) < 1


async def test_list_and_cancel_executors():
    _c, create = get_capability("reminders.create")
    _l, lst = get_capability("reminders.list")
    _x, cancel = get_capability("reminders.cancel")
    await create({"reminder_text": "Call mom", "due_at": _future(hours=1)})
    await create({"reminder_text": "Call dentist", "due_at": _future(hours=2)})
    assert len((await lst({}))["reminders"]) == 2
    with pytest.raises(ValueError, match="matches 2"):
        await cancel({"cancel_target": "call"})
    with pytest.raises(ValueError, match="No pending"):
        await cancel({"cancel_target": "zzz"})
    assert (await cancel({"cancel_target": "MOM"}))["text"] == "Call mom"
    assert [r["text"] for r in (await lst({}))["reminders"]] == ["Call dentist"]


async def test_dispatch_reminder_create_granted_and_missing_time_clarifies():
    await set_mission(title="Grow", statement="S.")
    ok = CapabilityInvocationCandidate(capability_id="reminders.create", raw_user_text="remind me",
                                       extra={"reminder_text": "stretch", "due_at": _future(minutes=30)})
    executed, relay, pending = await resolve_and_dispatch(ok)
    assert pending == [] and relay is None and "Reminder set for" in executed and "stretch" in executed

    no_time = CapabilityInvocationCandidate(capability_id="reminders.create", raw_user_text="remind me to stretch",
                                            extra={"reminder_text": "stretch"})
    executed, relay, _p = await resolve_and_dispatch(no_time)
    assert executed is None and "remind you" in relay.lower()


async def test_dispatch_reminder_cancel_requires_confirmation():
    await set_mission(title="Grow", statement="S.")
    await rapi.create_reminder("stretch", datetime.now(timezone.utc) + timedelta(hours=1))
    cand = CapabilityInvocationCandidate(capability_id="reminders.cancel", raw_user_text="cancel stretch",
                                         extra={"cancel_target": "stretch"})
    executed, relay, pending = await resolve_and_dispatch(cand)
    assert executed is None and len(pending) == 1
    assert len(await rapi.list_reminders()) == 1  # nothing cancelled before confirmation


# ---- extraction wiring (generic extra_parameters mechanism) ----

async def test_extraction_passes_extra_fields_for_selected_capability_only():
    from capabilities.registry import list_capabilities
    from capability_invocation.extraction import extract_candidate
    payload = {"capability_id": "reminders.create", "confidence": 0.9,
               "extra": {"reminder_text": "stretch", "due_at": "2026-09-21T09:00:00+05:45", "query": "leak", "cancel_target": "leak"}}
    with patch("capability_invocation.extraction.complete_json", new=AsyncMock(return_value=payload)) as m:
        cand = await extract_candidate("remind me to stretch tomorrow at 9", list_capabilities(), user_timezone="Asia/Kathmandu")
    assert cand.extra == {"reminder_text": "stretch", "due_at": "2026-09-21T09:00:00+05:45"}
    prompt = m.await_args.kwargs["prompt"]
    assert "web.search" in prompt and "reminders.create" in prompt and "query (for web.search" in prompt
