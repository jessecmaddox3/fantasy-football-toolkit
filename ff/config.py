"""Load local league references without contacting a provider."""
from __future__ import annotations
import os
import re
import tomllib
from dataclasses import fields
from pathlib import Path
from ff.leagues import LeagueRef


def load_leagues(path: str | Path | None = None) -> dict[str, LeagueRef]:
    source = Path(path or os.environ.get("FF_CONFIG", "leagues.toml")).expanduser()
    if not source.is_file():
        raise ValueError(f"League config not found: {source}. Copy examples/leagues.toml to leagues.toml and add your IDs, or run 'ff demo'.")
    with source.open("rb") as handle:
        document = tomllib.load(handle)
    if set(document) != {"leagues"} or not isinstance(document["leagues"], dict) or not document["leagues"]:
        raise ValueError("Config must contain at least one [leagues.KEY] table")
    refs = {}
    allowed = {f.name for f in fields(LeagueRef)} - {"key"}
    for key, original in document["leagues"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", key) or not isinstance(original, dict):
            raise ValueError("League keys must contain only letters, numbers, underscores or hyphens")
        row = dict(original)
        if set(row) - allowed:
            raise ValueError(f"Unknown fields in league {key}: {', '.join(sorted(set(row) - allowed))}")
        if row.get("platform") not in {"sleeper", "espn"}:
            raise ValueError(f"League {key}: platform must be sleeper or espn")
        for name in ("name", "note"):
            if name in row and not isinstance(row[name], str):
                raise ValueError(f"League {key}: {name} must be text")
        row.setdefault("name", key)
        if type(row.get("season")) is not int or not 2000 <= row["season"] <= 2100:
            raise ValueError(f"League {key}: season must be an integer between 2000 and 2100")
        required = ["league_id", "user_id"] if row["platform"] == "sleeper" else ["league_id", "team_id"]
        for name in set(required) | ({"draft_id", "user_id", "team_id"} & row.keys()):
            value = row.get(name)
            if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or int(value) <= 0:
                raise ValueError(f"League {key}: {name} must be a positive numeric ID")
            row[name] = int(value) if name == "team_id" else str(value)
        refs[key] = LeagueRef(key=key, **row)
    return refs
