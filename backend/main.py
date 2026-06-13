import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from analyzer import find_cross_market_edges
from fetcher_kalshi import fetch_kalshi_markets
from fetcher_odds import fetch_odds
from fetcher_polymarket import run_fetch as fetch_polymarket

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("sharpwatch")

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "")
FETCH_INTERVAL  = int(os.getenv("FETCH_INTERVAL", "120"))
CACHE_DIR       = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

app = FastAPI(title="SharpWatch")

_store: dict = {
    "polymarket": {},
    "kalshi":     [],
    "odds":       {},
    "cross":      [],
    "status":     {"polymarket": None, "kalshi": None, "odds": None},
}
_lock = asyncio.Lock()


def _write(name: str, data):
    (CACHE_DIR / f"{name}.json").write_text(json.dumps(data))


def _read(name: str):
    p = CACHE_DIR / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


async def refresh_polymarket():
    log.info("Refreshing Polymarket…")
    try:
        data = await fetch_polymarket(limit=200, min_value=100, top_n=200)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        async with _lock:
            _store["polymarket"] = data
            _store["status"]["polymarket"] = data["updated_at"]
        _write("polymarket", data)
        log.info("Polymarket done — %d markets", len(data.get("markets", [])))
    except Exception as e:
        log.error("Polymarket refresh failed: %s", e)


async def refresh_kalshi():
    log.info("Refreshing Kalshi…")
    try:
        markets = await fetch_kalshi_markets(limit=300)
        payload = {"markets": markets, "updated_at": datetime.now(timezone.utc).isoformat()}
        async with _lock:
            _store["kalshi"] = payload
            _store["status"]["kalshi"] = payload["updated_at"]
        _write("kalshi", payload)
        log.info("Kalshi done — %d markets", len(markets))
    except Exception as e:
        log.error("Kalshi refresh failed: %s", e)


async def refresh_odds():
    log.info("Refreshing sportsbook odds…")
    try:
        data = await fetch_odds(ODDS_API_KEY)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        async with _lock:
            _store["odds"] = data
            _store["status"]["odds"] = data["updated_at"]
        _write("odds", data)
        total = sum(len(v) for v in data.get("sports", {}).values())
        log.info("Odds done — %d events, %d credits remaining",
                 total, data.get("credits_remaining", 0))
    except Exception as e:
        log.error("Odds refresh failed: %s", e)


async def refresh_cross():
    async with _lock:
        odds_data = _store["odds"]
        kalshi    = (_store["kalshi"] or {}).get("markets", [])
        pm        = (_store["polymarket"] or {}).get("markets", [])
    if not odds_data.get("sports"):
        return
    try:
        edges = find_cross_market_edges(odds_data, kalshi, pm)
        async with _lock:
            _store["cross"] = edges
        log.info("Cross-market: %d edges found", len(edges))
    except Exception as e:
        log.error("Cross-market analysis failed: %s", e)


async def full_refresh():
    await asyncio.gather(
        refresh_polymarket(),
        refresh_kalshi(),
        refresh_odds(),
    )
    await refresh_cross()


@app.on_event("startup")
async def startup():
    # Load from disk cache
    for name in ("polymarket", "kalshi", "odds"):
        cached = _read(name)
        if cached:
            key = name if name != "odds" else "odds"
            _store[key if key != "kalshi" else "kalshi"] = cached
            if name == "kalshi":
                _store["kalshi"] = cached
            else:
                _store[name] = cached
            log.info("Loaded %s from disk cache", name)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(full_refresh,           "interval", seconds=FETCH_INTERVAL,
                      id="full", next_run_time=datetime.now())
    scheduler.start()
    app.state.scheduler = scheduler


@app.on_event("shutdown")
async def shutdown():
    app.state.scheduler.shutdown()


# ── API Routes ──────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    async with _lock:
        pm    = _store["polymarket"]
        kal   = _store["kalshi"]
        odds  = _store["odds"]
        cross = _store["cross"]
    return {
        "polymarket": {
            "ready":        bool(pm and pm.get("markets")),
            "market_count": len(pm.get("markets", [])) if pm else 0,
            "traders":      pm.get("traders_analyzed", 0) if pm else 0,
            "updated_at":   pm.get("updated_at") if pm else None,
        },
        "kalshi": {
            "ready":        bool(kal and kal.get("markets")),
            "market_count": len(kal.get("markets", [])) if kal else 0,
            "updated_at":   kal.get("updated_at") if kal else None,
        },
        "odds": {
            "ready":             bool(odds and odds.get("sports")),
            "has_key":           bool(ODDS_API_KEY and ODDS_API_KEY != "your_key_here"),
            "error":             odds.get("error") if odds else None,
            "sport_count":       len(odds.get("sports", {})) if odds else 0,
            "credits_remaining": odds.get("credits_remaining", 0) if odds else 0,
            "updated_at":        odds.get("updated_at") if odds else None,
        },
        "cross_market": {
            "edge_count": len(cross),
        },
        "refresh_interval": FETCH_INTERVAL,
    }


@app.get("/api/polymarket")
async def polymarket():
    async with _lock:
        d = _store["polymarket"]
    if not d:
        return JSONResponse({"status": "loading"}, status_code=202)
    return JSONResponse(d)


@app.get("/api/kalshi")
async def kalshi():
    async with _lock:
        d = _store["kalshi"]
    if not d:
        return JSONResponse({"status": "loading"}, status_code=202)
    return JSONResponse(d)


@app.get("/api/odds")
async def odds():
    async with _lock:
        d = _store["odds"]
    if not d:
        return JSONResponse({"status": "loading"}, status_code=202)
    return JSONResponse(d)


@app.get("/api/cross")
async def cross():
    async with _lock:
        return JSONResponse({"edges": _store["cross"]})


@app.get("/api/ev")
async def ev_bets():
    async with _lock:
        odds = _store["odds"]
    if not odds or not odds.get("sports"):
        return JSONResponse({"bets": []})
    bets = []
    for sport, events in odds["sports"].items():
        for ev in events:
            for bet in ev.get("ev_bets", []):
                bets.append({**bet, "sport": sport,
                             "game": f"{ev['away']} @ {ev['home']}",
                             "commence": ev.get("commence")})
    bets.sort(key=lambda b: b["ev_pct"], reverse=True)
    return JSONResponse({"bets": bets})


@app.get("/api/arb")
async def arb_bets():
    async with _lock:
        odds = _store["odds"]
    if not odds or not odds.get("sports"):
        return JSONResponse({"arbs": []})
    arbs = []
    for sport, events in odds["sports"].items():
        for ev in events:
            for op in ev.get("arb_ops", []):
                arbs.append({**op, "sport": sport,
                             "game": f"{ev['away']} @ {ev['home']}",
                             "commence": ev.get("commence")})
    arbs.sort(key=lambda a: a["profit_pct"], reverse=True)
    return JSONResponse({"arbs": arbs})


@app.get("/api/best-odds")
async def best_odds():
    async with _lock:
        odds = _store["odds"]
    if not odds or not odds.get("sports"):
        return JSONResponse({"games": []})
    games = []
    for sport, events in odds["sports"].items():
        for ev in events:
            games.append({
                "sport":       sport,
                "game":        f"{ev['away']} @ {ev['home']}",
                "home":        ev["home"],
                "away":        ev["away"],
                "commence":    ev.get("commence"),
                "best_odds":   ev.get("best_odds", {}),
                "fair_probs":  ev.get("fair_probs", {}),
                "bookmakers":  ev.get("bookmakers", {}),
            })
    return JSONResponse({"games": games})


# Serve frontend last
FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
