"""Fetches top trader positions from Polymarket's public API."""
from __future__ import annotations
import asyncio
import json
import logging
from collections import defaultdict

import aiohttp

log = logging.getLogger("sharpwatch.fetcher")

LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
POSITIONS_URL   = "https://data-api.polymarket.com/positions"
PAGE_SIZE       = 50
HEADERS         = {"User-Agent": "SharpWatch/1.0", "Accept": "application/json"}

CATEGORIES = [
    ("Sports",       ["nba", "nfl", "mlb", "nhl", "fifa", "world cup", "soccer", "football",
                      "basketball", "baseball", "tennis", "golf", "ufc", "mma", "boxing",
                      "champions league", "premier league", "la liga", "bundesliga", "serie a",
                      "nascar", "formula 1", "f1", "olympics", "esports", "cs:", "counter-strike",
                      "valorant", "league of legends", "dota", "vs ", " win on ", "iem", "vct",
                      "playoff", "championship", "super bowl", "world series", "stanley cup",
                      "knicks", "lakers", "celtics", "warriors", "heat", "spurs", "bulls",
                      "spread:", "over/under", "moneyline"]),
    ("Politics",     ["election", "president", "congress", "senate", "vote", "democrat",
                      "republican", "trump", "biden", "harris", "governor", "mayor", "parliament",
                      "legislation", "bill passes", "supreme court", "nominee", "impeach",
                      "white house", "house of rep", "filibuster", "poll ", "approval rating",
                      "primary", "caucus", "inaugur", "cabinet"]),
    ("Crypto",       ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
                      "blockchain", "defi", "nft", "token", "altcoin", "usdc", "usdt",
                      "binance", "coinbase", "doge", "dogecoin", "xrp", "ripple", "matic",
                      "polygon", "avalanche", "avax", "cardano", "ada", "shiba", "pepe",
                      "stablecoin", "sec crypto", "etf approved", "spot etf"]),
    ("Geopolitics",  ["war", "ceasefire", "nato", "russia", "ukraine", "china", "taiwan",
                      "israel", "iran", "north korea", "sanctions", "nuclear", "military",
                      "troops", "invasion", "conflict", "treaty", "un security", "g7", "g20",
                      "missile", "drone strike", "coup", "regime", "tariff", "trade war"]),
    ("Science & AI", ["spacex", "nasa", "rocket", "launch", "climate", "openai", "chatgpt",
                      "anthropic", "gemini", "gpt", "llm", "ai model", "artificial intelligence",
                      "earthquake", "hurricane", "temperature record", "co2", "asteroid",
                      "fda approv", "drug trial", "vaccine", "nobel prize"]),
    ("Entertainment",["oscar", "emmy", "grammy", "award", "box office", "movie", "film",
                      "album", "celebrity", "taylor swift", "kardashian", "superhero",
                      "netflix", "disney", "marvel", "reality tv",
                      "super bowl halftime", "miss universe", "miss world"]),
]

US_RESTRICTED_CATS = {"Politics"}


def categorize(title: str) -> str:
    text = title.lower()
    for name, keywords in CATEGORIES:
        if any(kw in text for kw in keywords):
            return name
    return "Other"


async def _get(session: aiohttp.ClientSession, url: str, params: dict) -> object:
    async with session.get(url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        return await r.json()


async def fetch_leaderboard(session: aiohttp.ClientSession, limit: int, window: str = "MONTH", by: str = "PNL") -> list[dict]:
    traders = []
    offset = 0
    while len(traders) < limit:
        batch = min(PAGE_SIZE, limit - len(traders))
        try:
            data = await _get(session, LEADERBOARD_URL, {"timePeriod": window, "orderBy": by, "limit": batch, "offset": offset})
        except Exception as e:
            log.warning("Leaderboard fetch failed at offset %d: %s", offset, e)
            break
        rows = data.get("data", data) if isinstance(data, dict) else data
        if not rows:
            break
        for row in rows:
            wallet = next((row.get(k) for k in ("proxyWallet", "wallet", "address", "user") if row.get(k)), None)
            name   = next((row.get(k) for k in ("userName", "name", "pseudonym") if row.get(k)), wallet)
            if wallet:
                traders.append({"rank": len(traders) + 1, "name": name, "wallet": wallet})
        if len(rows) < batch:
            break
        offset += batch
    return traders[:limit]


async def fetch_positions_for(session: aiohttp.ClientSession, wallet: str, min_value: float) -> list[dict]:
    try:
        data = await _get(session, POSITIONS_URL, {
            "user": wallet, "sizeThreshold": 1, "limit": 500,
            "sortBy": "CURRENT", "sortDirection": "DESC",
        })
    except Exception as e:
        log.debug("Positions fetch failed for %s: %s", wallet[:10], e)
        return []
    rows = data.get("data", data) if isinstance(data, dict) else data
    out = []
    for row in (rows or []):
        value = float(row.get("currentValue") or row.get("value") or 0)
        if value < min_value:
            continue
        out.append({
            "market": row.get("title") or row.get("market") or "(unknown)",
            "outcome": row.get("outcome") or "?",
            "slug":    row.get("slug") or row.get("conditionId") or "",
            "value":   value,
        })
    return out


async def run_fetch(limit: int = 200, min_value: float = 100.0, top_n: int = 200, concurrency: int = 10) -> dict:
    async with aiohttp.ClientSession() as session:
        log.info("Fetching top %d traders…", limit)
        traders = await fetch_leaderboard(session, limit)
        log.info("Got %d traders, fetching positions…", len(traders))

        sem = asyncio.Semaphore(concurrency)

        async def bounded(t):
            async with sem:
                positions = await fetch_positions_for(session, t["wallet"], min_value)
                await asyncio.sleep(0.1)
                return t, positions

        results = await asyncio.gather(*[bounded(t) for t in traders])

    market_holders: dict[str, list[dict]] = defaultdict(list)
    market_label:   dict[str, str]        = {}

    for trader, positions in results:
        seen = set()
        for p in positions:
            key = p["slug"] or p["market"]
            market_label[key] = p["market"]
            if key in seen:
                continue
            seen.add(key)
            market_holders[key].append({
                "trader":  trader["name"],
                "outcome": p["outcome"],
                "value":   p["value"],
                "rank":    trader["rank"],
            })

    ranked = sorted(
        market_holders.items(),
        key=lambda kv: (len(kv[1]), sum(h["value"] for h in kv[1])),
        reverse=True,
    )

    markets = []
    for key, holders in ranked[:top_n]:
        title = market_label[key]
        cat   = categorize(title)
        sides:       dict[str, int]   = defaultdict(int)
        side_values: dict[str, float] = defaultdict(float)
        for h in holders:
            sides[h["outcome"]]       += 1
            side_values[h["outcome"]] += h["value"]
        total_val = sum(h["value"] for h in holders)
        markets.append({
            "title":         title,
            "category":      cat,
            "us_restricted": cat in US_RESTRICTED_CATS,
            "trader_count":  len(holders),
            "total_value":   round(total_val, 2),
            "avg_position":  round(total_val / len(holders), 2) if holders else 0,
            "sides":         dict(sorted(sides.items(), key=lambda x: -x[1])),
            "side_values":   {k: round(v, 2) for k, v in sorted(side_values.items(), key=lambda x: -x[1])},
            "unanimous":     len(sides) == 1,
        })

    log.info("Done. %d markets found.", len(markets))
    return {
        "traders_analyzed": len(traders),
        "markets": markets,
    }
