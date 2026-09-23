"""web.search — grounded web search via Tavily (config: `search.*`).

Provider choice: Tavily over Brave/Serper/DuckDuckGo. Built for LLM
agents (clean `content` snippets, no HTML scraping), free tier of 1,000
credits/month with no card (a basic search costs 1), one JSON POST. DDG's
keyless endpoints are free but noisy with no SLA; Brave/Serper need more
result cleanup for the same job. Swapping later = one function + config.

Risk math (permission/risk_calculator.py: LOW=0..CRITICAL=3, summed):
  baseline LOW (0) + READ impact (0) + supports_undo=True (0) = 0 = LOW
  -> GRANTED, no confirmation. supports_undo=True follows
  calendar.read_events' precedent: a read has nothing to undo, so it
  takes no reversibility bump. (Had it been False: 0+0+1 = MEDIUM, still
  GRANTED — both are safe, True is the honest one.)

Returns at most `max_results` (default 6) {title, url, snippet} items,
snippets clipped — never page bodies (that would be a future web.fetch).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from capabilities.registry import register
from config.loader import load_config
from contracts.capability import Capability, ParamSpec
from contracts.enums import CapabilityType, ParamType, RiskLevel

_SNIPPET_CHARS = 300
_TITLE_CHARS = 150


async def _search(parameters: dict) -> dict[str, Any]:
    query = str(parameters["query"]).strip()
    if not query:
        raise ValueError("web.search needs a non-empty query")

    cfg = load_config()
    provider = cfg.get("search.provider", "tavily")
    if provider != "tavily":
        raise RuntimeError(f"Unsupported search provider '{provider}' — only 'tavily' is implemented")
    pcfg = cfg.get("search.providers.tavily", {}) or {}
    key = os.environ.get(pcfg.get("api_key_env", "TAVILY_API_KEY"))
    if not key:
        raise RuntimeError(f"Web search isn't configured — set {pcfg.get('api_key_env', 'TAVILY_API_KEY')} in .env")
    limit = int(pcfg.get("max_results", 6))
    timeout = float(pcfg.get("timeout_seconds", 8))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                pcfg.get("base_url", "https://api.tavily.com/search"),
                headers={"Authorization": f"Bearer {key}"},
                json={"query": query, "max_results": limit, "search_depth": "basic"},
            )
    except httpx.TimeoutException:
        raise RuntimeError(f"Web search timed out after {timeout:g}s — try again in a moment")
    except httpx.HTTPError as e:
        raise RuntimeError(f"Web search couldn't connect ({type(e).__name__})")

    if resp.status_code in (401, 403):
        raise RuntimeError("Web search API key was rejected — check TAVILY_API_KEY")
    if resp.status_code == 429:
        raise RuntimeError("Web search rate limit / free-tier quota reached")
    if resp.status_code != 200:
        raise RuntimeError(f"Web search failed (HTTP {resp.status_code})")

    try:
        raw = resp.json().get("results", [])
    except ValueError:
        raise RuntimeError("Web search returned a malformed response")
    results = [
        {
            "title": str(r.get("title", "")).strip()[:_TITLE_CHARS],
            "url": r.get("url", ""),
            "snippet": str(r.get("content", "")).strip()[:_SNIPPET_CHARS],
        }
        for r in raw[:limit] if r.get("url")
    ]
    return {"query": query, "results": results}


register(
    Capability(
        id="web.search", name="Web Search",
        description="Searches the web for a query and returns a few titled results with snippets to ground a reply in.",
        category="Web", capability_type=CapabilityType.PRIMITIVE,
        baseline_risk_level=RiskLevel.LOW, required_permissions=["web.read"], supports_undo=True,
        extra_parameters={
            "query": ParamSpec(
                type=ParamType.STRING, required=True,
                description="What should I search the web for? (a concise search query in the user's own terms)",
            ),
        },
    ),
    _search,
)
