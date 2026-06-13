"""Cross-market analysis: finds when prediction markets disagree with sportsbooks."""
from __future__ import annotations
import re


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _team_in(team: str, title: str) -> bool:
    t = _normalize(team)
    ti = _normalize(title)
    # Try full name or last word (city vs nickname)
    parts = t.split()
    return any(p in ti for p in parts if len(p) > 3)


def find_cross_market_edges(odds_data: dict, kalshi: list[dict], polymarket: list[dict]) -> list[dict]:
    """
    Match prediction market events to sportsbook events.
    Returns list of cross-market discrepancies where PM and sportsbook odds differ significantly.
    """
    edges = []

    for sport_label, events in odds_data.get("sports", {}).items():
        for ev in events:
            home = ev.get("home", "")
            away = ev.get("away", "")
            fair_probs = ev.get("fair_probs", {})
            if not fair_probs or len(fair_probs) < 2:
                continue

            # Try to match in Kalshi
            for km in kalshi:
                title = km.get("title", "")
                if not (_team_in(home, title) or _team_in(away, title)):
                    continue
                yes_price = km.get("yes_price")
                if yes_price is None:
                    continue
                # Figure out which outcome "Yes" refers to
                # Convention: Kalshi title is usually "Will [team] win?"
                yes_team = None
                for team in [home, away]:
                    if _team_in(team, title):
                        yes_team = team
                        break
                if not yes_team:
                    continue
                sb_prob = fair_probs.get(yes_team)
                if sb_prob is None:
                    continue
                pm_prob  = round(yes_price * 100, 1)
                diff     = round(pm_prob - sb_prob, 1)
                if abs(diff) < 5:
                    continue  # not interesting enough
                edges.append({
                    "source":       "Kalshi",
                    "pm_title":     title,
                    "game":         f"{away} @ {home}",
                    "sport":        sport_label,
                    "commence":     ev.get("commence"),
                    "yes_team":     yes_team,
                    "pm_prob":      pm_prob,
                    "sb_prob":      sb_prob,
                    "diff":         diff,
                    "edge":         "PM higher" if diff > 0 else "Books higher",
                    "best_sb_odds": ev.get("best_odds", {}).get(yes_team, {}),
                    "ticker":       km.get("ticker"),
                })

            # Try to match in Polymarket
            for pm in polymarket:
                title = pm.get("title", "")
                if not (_team_in(home, title) or _team_in(away, title)):
                    continue
                sides = pm.get("sides", {})
                if not sides:
                    continue
                yes_team = None
                for team in [home, away]:
                    if _team_in(team, title):
                        yes_team = team
                        break
                if not yes_team:
                    continue
                # Estimate PM prob from trader consensus
                yes_cnt = sides.get("Yes", 0)
                no_cnt  = sides.get("No",  0)
                total   = yes_cnt + no_cnt
                if total == 0:
                    continue
                pm_prob  = round(yes_cnt / total * 100, 1)
                sb_prob  = fair_probs.get(yes_team)
                if sb_prob is None:
                    continue
                diff = round(pm_prob - sb_prob, 1)
                if abs(diff) < 8:
                    continue
                edges.append({
                    "source":       "Polymarket",
                    "pm_title":     title,
                    "game":         f"{away} @ {home}",
                    "sport":        sport_label,
                    "commence":     ev.get("commence"),
                    "yes_team":     yes_team,
                    "pm_prob":      pm_prob,
                    "sb_prob":      sb_prob,
                    "diff":         diff,
                    "edge":         "PM higher" if diff > 0 else "Books higher",
                    "best_sb_odds": ev.get("best_odds", {}).get(yes_team, {}),
                    "trader_count": pm.get("trader_count", 0),
                })

    edges.sort(key=lambda x: abs(x["diff"]), reverse=True)
    return edges
