"""Optional DynastyProcess dynasty trade-value integration.

The public data repository uses GPL-3.0; it is downloaded only on request
and is not bundled or relicensed by this MIT package. See NOTICE.md."""

from __future__ import annotations

import io
import re

import httpx
import pandas as pd

from ff.cache import WEEK, fetch_text

VALUES_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values.csv"

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalize_name(name: str | None) -> str:
    """Join key shared with Sleeper names: lowercase, no punctuation, no suffix."""
    if not name or not isinstance(name, str):
        return ""
    cleaned = _PUNCT.sub("", name.lower())
    parts = [p for p in cleaned.split() if p not in _SUFFIXES]
    return " ".join(parts)


def values(*, client: httpx.Client | None = None, ttl: float = WEEK) -> pd.DataFrame:
    """One row per player with ``value_1qb`` / ``value_2qb`` and ECR ranks."""
    text = fetch_text(VALUES_URL, key="dynastyprocess_values", ttl=ttl, client=client)
    df = pd.read_csv(io.StringIO(text))
    for col in ("age", "ecr_1qb", "ecr_2qb", "ecr_pos", "value_1qb", "value_2qb"):
        if col in df.columns:
            # float even when a slice happens to be all-integer, so callers
            # get one dtype whether or not the file has missing values.
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["merge_name"] = df["player"].map(normalize_name)
    return df
