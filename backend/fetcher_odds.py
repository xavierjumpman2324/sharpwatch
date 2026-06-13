"""Fetches sportsbook odds from The-Odds-API (free tier: 500 req/month)."""
from __future__ import annotations
import logging
import aiohttp

log = logging.getLogger("sharpwatch.odds")

BASE = "https://api.the-odds-api.com/v4"

SPORTS = [
    ("americanfootball_nfl",  "NFL"),
    ("basketball_nba",        "NBA"),
    ("baseball_mlb",          "MLB"),
    ("icehockey_nhl",         "NHL"),
    ("soccer_epl",            "EPL"),
    ("soccer_fifa_world_cup", "World Cup"),
    ("mma_mixed_martial_arts","MMA/UFC"),
    ("basketball_ncaab",      "NCAAB"),
]

BOOKS = [
    "draftkings","fanduel","betmgm","caesars","pointsbetus",
    "bet365","pinnacle","bovada","mybookieag","williamhill_us",
]


def _to_decimal(american: int) -> float:
    if american > 0:
        return round(american / 100 + 1, 4)
    return round(100 / abs(american) + 1, 4)


def _implied(decimal_odds: float) -> float:
    return round(1 / decimal_odds, 6)


def _no_vig(probs: list[float]) -> list[float]:
    total = sum(probs)
    return [p / total for p in probs]


async def fetch_odds(api_key: str) -> dict:
    """Returns {sport_key: [events]} and remaining API credits info."""
    if not api_key or api_key == "your_key_here":
        return {"error": "no_key", "sports": {}, "credits_used": 0, "credits_remaining": 0}

    all_sports: dict[str, list] = {}
    credits_used = 0
    credits_remaining = 500

    async with aiohttp.ClientSession() as session:
        for sport_key, sport_label in SPORTS:
            params = {
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "bookmakers": ",".join(BOOKS),
            }
            try:
                async with session.get(
                    f"{BASE}/sports/{sport_key}/odds",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    credits_used      = int(r.headers.get("x-requests-used", credits_used))
                    credits_remaining = int(r.headers.get("x-requests-remaining", credits_remaining))
                    if r.status == 401:
                        return {"error": "invalid_key", "sports": {}, "credits_used": 0, "credits_remaining": 0}
                    if r.status == 422:
                        continue  # sport not currently available
                    r.raise_for_status()
                    events = await r.json()
            except Exception as e:
                log.warning("Odds fetch failed for %s: %s", sport_key, e)
                continue

            processed = []
            for ev in events:
                game = {
                    "id":           ev.get("id"),
                    "sport":        sport_label,
                    "sport_key":    sport_key,
                    "home":         ev.get("home_team", ""),
                    "away":         ev.get("away_team", ""),
                    "commence":     ev.get("commence_time"),
                    "bookmakers":   {},
                    "best_odds":    {},
                    "ev_bets":      [],
                    "arb_ops":      [],
                }

                # Collect all h2h odds by outcome
                outcome_odds: dict[str, dict[str, float]] = {}  # outcome -> {book: decimal}
                for bm in ev.get("bookmakers", []):
                    book = bm["key"]
                    for market in bm.get("markets", []):
                        if market["key"] != "h2h":
                            continue
                        for out in market.get("outcomes", []):
                            name = out["name"]
                            price = out["price"]
                            dec = _to_decimal(price) if isinstance(price, int) else float(price)
                            outcome_odds.setdefault(name, {})[book] = dec
                            game["bookmakers"].setdefault(book, {})[name] = {
                                "american": price,
                                "decimal":  dec,
                            }

                if not outcome_odds:
                    continue

                # Best odds per outcome
                for outcome, books in outcome_odds.items():
                    best_book = max(books, key=lambda b: books[b])
                    game["best_odds"][outcome] = {
                        "book":     best_book,
                        "decimal":  books[best_book],
                        "american": _to_american(books[best_book]),
                    }

                # No-vig consensus probability
                outcomes = list(outcome_odds.keys())
                if len(outcomes) >= 2:
                    # Use average implied prob across all books
                    avg_probs = []
                    for outcome in outcomes:
                        imp = [_implied(d) for d in outcome_odds[outcome].values()]
                        avg_probs.append(sum(imp) / len(imp))
                    fair_probs = _no_vig(avg_probs)

                    # +EV check
                    for i, outcome in enumerate(outcomes):
                        fair_p = fair_probs[i]
                        for book, dec_odds in outcome_odds[outcome].items():
                            ev_pct = round((dec_odds * fair_p - 1) * 100, 2)
                            if ev_pct >= 2.0:
                                game["ev_bets"].append({
                                    "outcome":  outcome,
                                    "book":     book,
                                    "odds":     _to_american(dec_odds),
                                    "ev_pct":   ev_pct,
                                    "fair_prob":round(fair_p * 100, 1),
                                })

                    # Arb check (2-way)
                    if len(outcomes) == 2:
                        best_dec = [max(outcome_odds[o].values()) for o in outcomes]
                        best_books = [max(outcome_odds[o], key=lambda b: outcome_odds[o][b]) for o in outcomes]
                        arb_pct = round((1 - sum(1/d for d in best_dec)) * 100, 2)
                        if arb_pct > 0:
                            game["arb_ops"].append({
                                "profit_pct": arb_pct,
                                "legs": [
                                    {"outcome": outcomes[j], "book": best_books[j],
                                     "odds": _to_american(best_dec[j])}
                                    for j in range(2)
                                ],
                            })

                    game["fair_probs"] = {outcomes[i]: round(fair_probs[i]*100,1) for i in range(len(outcomes))}

                processed.append(game)

            if processed:
                all_sports[sport_label] = processed
                log.info("Odds: %s — %d events", sport_label, len(processed))

    return {
        "sports": all_sports,
        "credits_used": credits_used,
        "credits_remaining": credits_remaining,
        "error": None,
    }


def _to_american(decimal: float) -> int:
    if decimal >= 2.0:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))
