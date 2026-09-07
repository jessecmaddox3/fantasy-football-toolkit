"""Vegas lines from the public ESPN scoreboard, and implied team totals.

The scoreboard is a keyless source of posted spreads and totals.  What a lineup engine actually wants is neither of those: it
wants the **implied team total**, the market's central estimate of how many
points one team will score.

    home = over_under / 2 - spread / 2
    away = over_under / 2 + spread / 2

ESPN reports ``spread`` from the *home* team's side, so a negative spread means
the home team is favoured and a positive one means the road team is.  Getting
that sign backwards inverts every adjustment downstream, which is why
:func:`implied_totals` is tested against a game of each kind.

Team codes: ESPN's scoreboard says ``WSH`` where Sleeper's rosters say ``WAS``,
and the Rams are ``LAR`` here, ``LAR`` on Sleeper and ``LA`` in nflverse.  Every
returned mapping is keyed by the Sleeper spelling *and* the nflverse one, the
same convention ``nflverse.STADIUM_COORDS`` uses.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx

from ff.cache import HOUR, USER_AGENT, fetch_json
from ff.sources.nflverse import TEAM_ALIASES, games

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

#: ESPN's public site API 403s on a bare product User-Agent (verified
#: 2026-08-30: ``ff-agent/0.1 (personal fantasy tooling)`` and ``Mozilla/5.0``
#: both refused, ``python-httpx/...``, ``curl/...`` and ``python-requests/...``
#: all served).  Its edge appears to allow known HTTP-client tokens, so the
#: package identifier is kept and the httpx token appended rather than pretending
#: to be a browser.
SCOREBOARD_HEADERS = {"User-Agent": f"{USER_AGENT} python-httpx/{httpx.__version__}"}

#: ESPN scoreboard abbreviation -> the Sleeper spelling used on rosters.  The
#: Rams need no entry: both sides say ``LAR``; it is nflverse that says ``LA``.
ESPN_TEAM_ALIASES = {"WSH": "WAS"}

#: Used only when no game on the slate has a posted line, so that the implied
#: total multiplier degrades to 1.0 rather than to nonsense.  Roughly a 45-point
#: game split evenly.
DEFAULT_TEAM_TOTAL = 22.5

ODDS_TTL = HOUR


def sleeper_abbrev(code: str | None) -> str | None:
    """Map an ESPN scoreboard team code onto the Sleeper spelling."""
    if not code:
        return None
    return ESPN_TEAM_ALIASES.get(code, code)


@dataclass(frozen=True)
class GameOdds:
    """One game's market, already resolved into per-team implied totals."""

    game_id: str
    home: str
    away: str
    kickoff: dt.datetime | None
    neutral_site: bool = False
    venue: str = ""
    spread: float | None = None          # from the home team's side
    over_under: float | None = None
    provider: str = ""

    @property
    def matchup(self) -> str:
        return f"{self.away}@{self.home}"

    @property
    def home_total(self) -> float | None:
        if self.over_under is None or self.spread is None:
            return None
        return self.over_under / 2 - self.spread / 2

    @property
    def away_total(self) -> float | None:
        if self.over_under is None or self.spread is None:
            return None
        return self.over_under / 2 + self.spread / 2

    def total_for(self, team: str) -> float | None:
        if team == self.home:
            return self.home_total
        if team == self.away:
            return self.away_total
        return None

    def opponent_of(self, team: str) -> str | None:
        if team == self.home:
            return self.away
        if team == self.away:
            return self.home
        return None


def _kickoff(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _best_line(entries: list[dict]) -> dict:
    """The highest-priority provider that actually posted a spread and a total."""
    usable = [
        e for e in entries
        if e.get("spread") is not None and e.get("overUnder") is not None
    ]
    if not usable:
        return {}
    return min(usable, key=lambda e: (e.get("provider") or {}).get("priority", 99))


def scoreboard(
    date: dt.date,
    *,
    client: httpx.Client | None = None,
    ttl: float = ODDS_TTL,
) -> dict:
    """Raw scoreboard payload for one calendar date."""
    stamp = f"{date:%Y%m%d}"
    return fetch_json(
        SCOREBOARD_URL,
        key=f"espn_scoreboard_{stamp}",
        ttl=ttl,
        params={"dates": stamp},
        headers=SCOREBOARD_HEADERS,
        client=client,
    )


def game_odds(
    date: dt.date,
    *,
    client: httpx.Client | None = None,
    ttl: float = ODDS_TTL,
) -> list[GameOdds]:
    """Every game on ``date``, with its line where one has been posted."""
    raw = scoreboard(date, client=client, ttl=ttl)

    out: list[GameOdds] = []
    for event in raw.get("events") or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]

        sides = {
            c.get("homeAway"): sleeper_abbrev((c.get("team") or {}).get("abbreviation"))
            for c in comp.get("competitors") or []
        }
        if not sides.get("home") or not sides.get("away"):
            continue

        line = _best_line(comp.get("odds") or [])
        out.append(
            GameOdds(
                game_id=str(event.get("id") or comp.get("id") or ""),
                home=sides["home"],
                away=sides["away"],
                kickoff=_kickoff(comp.get("date") or event.get("date")),
                neutral_site=bool(comp.get("neutralSite")),
                venue=(comp.get("venue") or {}).get("fullName", ""),
                spread=line.get("spread"),
                over_under=line.get("overUnder"),
                provider=(line.get("provider") or {}).get("name", ""),
            )
        )
    return out


def _alias_in_place(mapping: dict) -> dict:
    for sleeper_code, nflverse_code in TEAM_ALIASES.items():
        if sleeper_code in mapping:
            mapping.setdefault(nflverse_code, mapping[sleeper_code])
        elif nflverse_code in mapping:
            mapping.setdefault(sleeper_code, mapping[nflverse_code])
    return mapping


def implied_totals(
    date: dt.date,
    *,
    client: httpx.Client | None = None,
    ttl: float = ODDS_TTL,
) -> dict[str, float]:
    """``{team: implied team total}`` for one date.

    Teams whose game has no posted line are absent rather than defaulted, so a
    caller can tell "the market expects 27" from "the market has not spoken".
    """
    out: dict[str, float] = {}
    for game in game_odds(date, client=client, ttl=ttl):
        if game.home_total is None:
            continue
        out[game.home] = game.home_total
        out[game.away] = game.away_total
    return _alias_in_place(out)


def week_game_odds(
    season: int,
    week: int,
    *,
    client: httpx.Client | None = None,
    ttl: float = ODDS_TTL,
) -> list[GameOdds]:
    """Every game in an NFL week, across the four days a week can span."""
    sched = games(season, client=client)
    days = sorted({
        d for d in sched.loc[sched["week"] == week, "gameday"].dropna().unique()
    })

    out: list[GameOdds] = []
    for day in days:
        out.extend(game_odds(dt.date.fromisoformat(str(day)), client=client, ttl=ttl))
    return out


def week_implied_totals(
    season: int,
    week: int,
    *,
    client: httpx.Client | None = None,
    ttl: float = ODDS_TTL,
) -> dict[str, float]:
    """``{team: implied team total}`` for a whole NFL week."""
    out: dict[str, float] = {}
    for game in week_game_odds(season, week, client=client, ttl=ttl):
        if game.home_total is None:
            continue
        out[game.home] = game.home_total
        out[game.away] = game.away_total
    return _alias_in_place(out)


def baseline_total(totals: dict[str, float]) -> float:
    """The slate's average implied total: the zero point of the multiplier.

    Self-normalising on purpose.  A week of low totals should not push every
    player down; what matters is how a team's total compares to the rest of the
    slate.  Alias spellings are skipped so the Rams are not counted twice.
    """
    aliases = set(TEAM_ALIASES.values())
    real = [v for k, v in totals.items() if k not in aliases]
    if not real:
        return DEFAULT_TEAM_TOTAL
    return sum(real) / len(real)
