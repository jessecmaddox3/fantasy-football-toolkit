"""Read public weekly FantasyPros consensus, with explicit scope and freshness.

The adapter reads the ECR JSON when available on classic public ranking pages,
without credentials. Availability and access terms belong to the provider.
Draft CSV files are excluded. Consensus is a reference, not a substitute for
projections scored under the league's custom rules.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from zoneinfo import ZoneInfo

import httpx

from ff.cache import HOUR, _request, _write, cache_path

UTC = dt.timezone.utc
EASTERN = ZoneInfo("America/New_York")
MAX_SOURCE_AGE = dt.timedelta(hours=48)
PAGES = {
    ("PPR", "FLX"): "ppr-flex.php",
    ("HALF", "FLX"): "half-point-ppr-flex.php",
    ("STD", "FLX"): "flex.php",
    ("STD", "QB"): "qb.php",
    ("STD", "DST"): "dst.php",
    ("STD", "K"): "k.php",
}


class RankingsError(ValueError):
    """Consensus could not be verified for the requested scope and freshness."""


def _positive_number(value):
    if type(value) not in (str, int, float):
        raise RankingsError("rank/timestamp must be a number")
    try:
        parsed = float(value)
    except (ValueError, OverflowError) as exc:
        raise RankingsError("rank/timestamp must be a valid number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise RankingsError("rank/timestamp must be finite and positive")
    return parsed


def failure_reason(error: Exception) -> str:
    """Never copy URLs, headers or cookies out of HTTP exception messages."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.HTTPError):
        return type(error).__name__
    if isinstance(error, OSError):
        return "local cache unavailable"
    if isinstance(error, RankingsError):
        return str(error)
    return type(error).__name__


def _snapshot(data, retrieved, *, season, week, scoring, position, now, url):
    if not isinstance(data, dict):
        raise RankingsError("consensus JSON must be an object")
    expected = {
        "sport": "NFL", "ranking_type_name": "weekly", "year": str(season),
        "week": str(week), "scoring": scoring, "position_id": position,
    }
    for field, value in expected.items():
        if str(data.get(field)) != value:
            raise RankingsError(f"consensus scope mismatch: {field}")
    try:
        published = dt.datetime.fromtimestamp(_positive_number(data["last_updated_ts"]), UTC)
        rows = data["players"]
        if type(data["count"]) not in (str, int) or not str(data["count"]).isdecimal():
            raise RankingsError("player count must be a positive whole number")
        count = int(data["count"])
        if not isinstance(rows, list) or not rows or len(rows) != count:
            raise RankingsError("empty or incomplete consensus player list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("player_name"), str) or not row["player_name"].strip():
                raise RankingsError("player rank/name is missing")
            _positive_number(row["rank_ecr"])
            allowed = "(?:RB|WR|TE)" if position == "FLX" else re.escape(position)
            if not isinstance(row.get("pos_rank"), str) or not re.fullmatch(allowed + r"[1-9][0-9]*", row["pos_rank"]):
                raise RankingsError("invalid positional rank for this scope")
            if row.get("player_team_id") is not None and not isinstance(row["player_team_id"], str):
                raise RankingsError("player team must be text or absent")
        experts = data.get("total_experts")
        if experts is not None and (type(experts) not in (str, int) or not str(experts).isdecimal()):
            raise RankingsError("expert count must be a nonnegative whole number")
    except RankingsError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise RankingsError("missing or invalid consensus metadata") from exc
    age = now - published
    if age > MAX_SOURCE_AGE or age < -dt.timedelta(minutes=5):
        raise RankingsError(f"source timestamp is stale or in the future: {published.isoformat()}")
    return {
        "source_url": url, "season": season, "week": week,
        "scoring": scoring, "position": position,
        "retrieved_at": retrieved.isoformat(), "published_at": published.isoformat(),
        "count": count, "experts": data.get("total_experts"), "players": rows,
    }


def weekly_rankings(
    season: int, week: int, *, scoring: str = "PPR", position: str = "FLX",
    client: httpx.Client | None = None, ttl: float = 6 * HOUR,
    now: dt.datetime | None = None,
) -> dict:
    """Refresh after six hours; reject wrong-week, stale or count-inconsistent responses.

    ``ttl=0`` forces a network refresh and saves the verified response. An expired
    cache is never used after a failed fetch. Both the provider timestamp and the
    actual retrieval timestamp survive cache hits unchanged.
    """
    if type(season) is not int or not 2000 <= season <= 2100:
        raise RankingsError("season must be a whole number between 2000 and 2100")
    if type(week) is not int or not 1 <= week <= 18:
        raise RankingsError("week must be a whole number between 1 and 18")
    if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl < 0:
        raise RankingsError("cache lifetime must be finite and nonnegative")
    if not isinstance(scoring, str) or not isinstance(position, str):
        raise RankingsError("scoring and position must be supported text scopes")
    now = now or dt.datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    try:
        url = "https://www.fantasypros.com/nfl/rankings/" + PAGES[(scoring, position)]
    except KeyError as exc:
        raise RankingsError(f"unsupported scoring/position: {scoring}/{position}") from exc
    path = cache_path(f"fantasypros_weekly_{season}_{week}_{scoring}_{position}")
    check = dict(season=season, week=week, scoring=scoring, position=position, now=now, url=url)
    if ttl > 0 and path.exists():
        try:
            cached = json.loads(path.read_text())
            retrieved = dt.datetime.fromisoformat(cached["retrieved_at"])
            if 0 <= (now - retrieved).total_seconds() < ttl:
                result = _snapshot(cached["data"], retrieved, **check)
                result["cache_saved"] = True
                return result
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Bad/stale cache requires a real refresh, not a silent fallback.

    response = _request(url, params={"week": week}, client=client, timeout=15)
    match = re.search(r"\bvar\s+ecrData\s*=\s*", response.text)
    if not match:
        raise RankingsError("public page did not contain consensus JSON")
    try:
        data, _ = json.JSONDecoder().raw_decode(response.text[match.end():])
    except ValueError as exc:
        raise RankingsError("consensus JSON could not be read") from exc
    snapshot = _snapshot(data, now, **check)
    try:
        _write(path, json.dumps({"retrieved_at": now.isoformat(), "data": data}))
        snapshot["cache_saved"] = True
    except OSError:
        snapshot["cache_saved"] = False
        snapshot["cache_note"] = "Valid response, but the local cache could not be saved."
    return snapshot


def _name_key(name):
    name = re.sub(r"\s+(?:jr\.?|sr\.?|ii|iii|iv)$", "", str(name).lower())
    return re.sub(r"[^a-z]", "", name)


def _team_key(team):
    return {"LA": "LAR", "JAC": "JAX", "WSH": "WAS"}.get(team, team)


def lineup_notes(lines, season, week, *, reception_points, client=None) -> tuple[str, ...]:
    """Attach dated consensus ranks for rostered players without changing starts."""
    scoring = "PPR" if reception_points >= 1 else "HALF" if reception_points >= 0.5 else "STD"
    groups = {}
    for line in lines:
        position = "FLX" if line.pos in {"RB", "WR", "TE"} else "DST" if line.pos in {"DEF", "DST"} else line.pos
        if position in {"FLX", "QB", "DST", "K"}:
            groups.setdefault(position, []).append(line)
    notes = []
    for position, players in groups.items():
        fmt = scoring if position == "FLX" else "STD"
        try:
            data = weekly_rankings(season, week, scoring=fmt, position=position, client=client)
        except (httpx.HTTPError, RankingsError, OSError) as exc:
            notes.append(f"FantasyPros UNAVAILABLE for {season} week {week} {fmt}/{position}: {failure_reason(exc)}. No stale rankings used.")
            continue
        stamp = lambda s: dt.datetime.fromisoformat(s).astimezone(EASTERN).strftime("%b %d %H:%M ET")
        notes.append(
            f"FantasyPros {season} week {week} {fmt}/{position}: source updated {stamp(data['published_at'])}; "
            f"retrieved {stamp(data['retrieved_at'])}; {data['count']} players. {data['source_url']}"
        )
        if data.get("cache_note"):
            notes.append(data["cache_note"])
        index = {}
        teams = {}
        for row in data["players"]:
            key = (_name_key(row["player_name"]), _team_key(row.get("player_team_id")))
            index.setdefault(key, []).append(row)
            if row.get("player_team_id"):
                teams.setdefault(_team_key(row["player_team_id"]), []).append(row)
        matched, missing = [], []
        for player in players:
            if position == "DST":
                found = teams.get(_team_key(player.team), []) if player.team else []
            else:
                found = index.get((_name_key(player.name), _team_key(player.team)), [])
            if len(found) == 1:
                status = "current starter" if player.started else "current bench"
                matched.append(f"{status} {player.name} {found[0]['pos_rank']}")
            else:
                missing.append(player.name)
        if matched:
            notes.append("FantasyPros positional ECR: " + "; ".join(matched))
        if missing:
            notes.append("FantasyPros unmatched players: " + ", ".join(missing))
    if "FLX" in groups and reception_points not in (0, 0.5, 1):
        notes.append(f"FantasyPros {scoring} is only a reference bucket for this custom reception scoring; it is not an equivalent scoring format.")
    if groups:
        notes.append("FantasyPros ranks are a consensus reference; custom scoring, TE premium and passing-TD rules remain in the league projections. ECR is not ADP.")
    return tuple(notes)
