"""Cross-market analysis: finds when prediction markets disagree with sportsbooks."""
from __future__ import annotations
import re


_STOPWORDS = {"the", "will", "win", "beat", "cover", "over", "under", "and", "for",
              "city", "bay", "new", "los", "san", "las", "fort", "port", "east", "west"}

_NICKNAMES: dict[str, list[str]] = {
    "golden state warriors": ["warriors", "golden state", "gsw"],
    "los angeles lakers":    ["lakers", "la lakers"],
    "los angeles clippers":  ["clippers", "la clippers"],
    "new york knicks":       ["knicks", "new york"],
    "boston celtics":        ["celtics", "boston"],
    "miami heat":            ["heat", "miami"],
    "oklahoma city thunder": ["thunder", "okc"],
    "minnesota timberwolves":["timberwolves", "wolves", "minnesota"],
    "new york yankees":      ["yankees", "new york"],
    "los angeles dodgers":   ["dodgers", "la dodgers"],
    "kansas city chiefs":    ["chiefs", "kansas city", "kc chiefs"],
    "philadelphia eagles":   ["eagles", "philadelphia", "philly"],
    "san francisco 49ers":   ["49ers", "niners", "san francisco"],
    "new england patriots":  ["patriots", "new england", "pats"],
    "dallas cowboys":        ["cowboys", "dallas"],
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _team_in(team: str, title: str) -> bool:
    t  = _normalize(team)
    ti = _normalize(title)

    # Check nickname map first
    for full, aliases in _NICKNAMES.items():
        if t == _normalize(full) or t in [_normalize(a) for a in aliases]:
            if any(_normalize(a) in ti for a in aliases):
                return True

    # Fallback: use significant words only (exclude stopwords and short words)
    parts = [p for p in t.split() if len(p) > 4 and p not in _STOPWORDS]
    if not parts:
        # Short team name — require the full normalized name
        return t in ti
    # Require the team nickname (last meaningful word) OR 2+ parts matching
    nickname = parts[-1]
    if nickname in ti:
        return True
    return sum(1 for p in parts if p in ti) >= 2


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
