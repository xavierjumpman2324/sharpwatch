"""Fetches top Kalshi prediction markets."""
from __future__ import annotations
import logging
from collections import defaultdict
import aiohttp

log = logging.getLogger("sharpwatch.kalshi")

BASE = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"Accept": "application/json", "User-Agent": "SharpWatch/1.0"}

CATEGORY_KEYWORDS = {
    "Sports":        ["nba","nfl","mlb","nhl","soccer","football","basketball","baseball",
                      "tennis","golf","ufc","boxing","world cup","champions","playoff","super bowl"],
    "Politics":      ["election","president","congress","senate","vote","republican","democrat",
                      "governor","white house","supreme court","trump","harris"],
    "Crypto":        ["bitcoin","btc","ethereum","eth","crypto","solana","doge","xrp","coinbase"],
    "Geopolitics":   ["war","russia","ukraine","china","taiwan","israel","iran","nato","ceasefire","nuclear"],
    "Economics":     ["fed","rate","cpi","inflation","gdp","recession","unemployment","fomc",
                      "interest rate","jobs report","s&p","nasdaq","dow"],
    "Science & AI":  ["spacex","nasa","openai","chatgpt","ai","climate","fda","hurricane","earthquake"],
    "Entertainment": ["oscar","emmy","grammy","box office","movie","album","celebrity"],
}

def categorize(title: str) -> str:
    t = title.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in t for k in kws):
            return cat
    return "Other"


async def fetch_kalshi_markets(limit: int = 200) -> list[dict]:
    results = []
    cursor = None

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        while len(results) < limit:
            params: dict = {"limit": min(200, limit - len(results)), "status": "open"}
            if cursor:
                params["cursor"] = cursor
            try:
                async with session.get(f"{BASE}/markets", params=params,
                                       timeout=aiohttp.ClientTimeout(total=20)) as r:
                    r.raise_for_status()
                    data = await r.json()
            except Exception as e:
                log.warning("Kalshi fetch error: %s", e)
                break

            markets = data.get("markets", [])
            if not markets:
                break

            for m in markets:
                # API returns prices in dollars (0.00–1.00) with _dollars suffix
                yes_ask = float(m.get("yes_ask_dollars") or m.get("last_price_dollars") or 0)
                no_ask  = float(m.get("no_ask_dollars")  or 0)
                if not yes_ask and not no_ask:
                    continue
                volume  = float(m.get("volume_fp") or m.get("volume", 0) or 0)
                title   = m.get("title") or m.get("subtitle") or ""
                if not title:
                    continue
                results.append({
                    "ticker":        m.get("ticker", ""),
                    "title":         title,
                    "category":      categorize(title),
                    "yes_price":     round(yes_ask, 4) if yes_ask else None,
                    "no_price":      round(no_ask,  4) if no_ask  else None,
                    "volume":        volume,
                    "open_interest": float(m.get("open_interest_fp") or 0),
                    "close_time":    m.get("close_time") or m.get("expiration_time"),
                })

            cursor = data.get("cursor")
            if not cursor:
                break

    results.sort(key=lambda x: x["open_interest"], reverse=True)
    log.info("Kalshi: %d markets fetched", len(results))
    return results[:limit]
