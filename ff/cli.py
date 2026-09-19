"""Read-only fantasy football draft boards, lineup analysis, and weekly briefs.

Run ff demo for an offline example or configure your own leagues in TOML."""

from __future__ import annotations

import argparse
import sys
import httpx
from dataclasses import replace

import pandas as pd

from ff import leagues, notify, roster
from ff.engine import brief as brief_engine
from ff.engine import lineup as lineup_engine
from ff.engine.value import FLEX_ELIGIBILITY, value_over_replacement
from ff.leagues import LeagueRules, adp_field
from ff.config import load_leagues
from ff.sources import dynastyprocess, espn, fantasypros, nflverse, sleeper

DEFAULT_SEASON = 2026


def current_week(season: int = DEFAULT_SEASON, *, require_season_match: bool = False) -> int:
    """The NFL week Sleeper says we are on.

    Sleeper's ``/state/nfl`` is authoritative and free, which lets
    scheduled jobs run without anyone hand-editing a week number every Tuesday.
    Before the season opens it reports week 1.
    """
    state = sleeper.state()
    if str(state.get("season")) != str(season):
        if require_season_match:
            raise ValueError("Sleeper reports a different season; pass --week explicitly")
        return 1
    return int(state.get("week") or state.get("display_week") or 1)


def startable_positions(rules: LeagueRules) -> set[str]:
    """Positions the league can actually start, flex eligibility included."""
    out: set[str] = set()
    for slot in rules.roster_slots:
        if slot in FLEX_ELIGIBILITY:
            out.update(FLEX_ELIGIBILITY[slot])
        else:
            out.add("DEF" if slot == "DST" else slot)
    return out


def scoring_caveats(rules: LeagueRules) -> list[str]:
    """Known places where a projected total under-counts real scoring.

    Both caveats hit kickers and defenses only, so a board is trustworthy at the
    positions that decide a draft and explicitly not at the ones that do not.
    """
    notes: list[str] = []
    starts_k = "K" in rules.roster_slots
    starts_dst = "DEF" in rules.roster_slots or "DST" in rules.roster_slots

    if rules.unmapped_stat_ids and (starts_k or starts_dst):
        notes.append(
            f"K/DST scoring is incomplete: {len(rules.unmapped_stat_ids)} ESPN "
            "scoring items have no unambiguous Sleeper equivalent and score 0."
        )
    if starts_k and not rules.unmapped_stat_ids:
        notes.append(
            "Kickers are under-projected: Sleeper's season projections carry no "
            "field goal bucket under 40 yards, so short makes score nothing."
        )
    return notes


def build_board(
    projections: pd.DataFrame,
    rules: LeagueRules,
    *,
    byes: dict[str, int],
    top: int = 200,
) -> pd.DataFrame:
    """Ranked draft board for one league."""
    df = projections[projections["pos"].isin(startable_positions(rules))].copy()
    scored = value_over_replacement(df, rules)

    adp_col = adp_field(rules)
    out = pd.DataFrame(
        {
            "name": scored["name"],
            "pos": scored["pos"],
            "team": scored["team"],
            "bye": scored["team"].map(byes).astype("Int64"),
            "adp": scored[adp_col] if adp_col in scored.columns else pd.NA,
            "proj": scored["proj_points"].round(1),
            "vor": scored["vor"].round(1),
        }
    ).head(top)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out.reset_index(drop=True)


def build_rookie_board(
    projections: pd.DataFrame,
    rules: LeagueRules,
    dp_values: pd.DataFrame,
    *,
    byes: dict[str, int],
    top: int = 60,
) -> pd.DataFrame:
    """First-year players ordered by dynasty superflex ADP.

    ``adp_dynasty_2qb`` is the market; DynastyProcess ``value_2qb`` is a model.
    Where they disagree is the reason to look twice at a pick.
    """
    df = projections[
        (projections["pos"].isin(startable_positions(rules)))
        & (projections["years_exp"] == 0)
    ].copy()

    scored = value_over_replacement(df, rules)
    adp_col = adp_field(rules)

    vals = dp_values[["merge_name", "value_2qb", "ecr_2qb"]].drop_duplicates("merge_name")
    scored["merge_name"] = scored["name"].map(dynastyprocess.normalize_name)
    joined = scored.merge(vals, on="merge_name", how="left")

    out = pd.DataFrame(
        {
            "name": joined["name"],
            "pos": joined["pos"],
            "team": joined["team"],
            "bye": joined["team"].map(byes).astype("Int64"),
            "adp": joined[adp_col] if adp_col in joined.columns else pd.NA,
            "value_2qb": joined["value_2qb"],
            "ecr_2qb": joined["ecr_2qb"],
            "proj": joined["proj_points"].round(1),
        }
    )
    out = out.sort_values("adp", na_position="last").head(top)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out.reset_index(drop=True)


def render_table(df: pd.DataFrame) -> str:
    """Fixed-width plain text, readable in a terminal during a live draft."""
    cols = list(df.columns)

    def cell(v) -> str:
        if v is None or (isinstance(v, float) and v != v) or v is pd.NA:
            return "-"
        if isinstance(v, float):
            return f"{v:.1f}"
        return str(v)

    body = [[cell(v) for v in row] for row in df.itertuples(index=False)]
    widths = [max(len(c), *(len(r[i]) for r in body)) if body else len(c)
              for i, c in enumerate(cols)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for r in body:
        lines.append("  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip())
    return "\n".join(lines)


def _load_rules(ref, schedule: pd.DataFrame) -> LeagueRules:
    if ref.platform == "sleeper":
        raw = sleeper.league(ref.league_id)
    else:
        raw = espn.league(int(ref.league_id), season=ref.season)
    return leagues.normalize_league(raw, key=ref.key, schedule=schedule)


def cmd_board(args: argparse.Namespace) -> int:
    ref = args.refs[args.league]
    schedule = nflverse.games(args.season)
    rules = _load_rules(ref, schedule)
    byes = nflverse.byes(args.season)
    projections = sleeper.season_projections(args.season)
    if projections.empty:
        print(f"Sleeper has no season projections for {args.season} yet.")
        return 1

    if args.rookies:
        board = build_rookie_board(
            projections, rules, dynastyprocess.values(), byes=byes, top=args.top
        )
        title = f"{ref.name} - rookie board ({adp_field(rules)} x DynastyProcess value_2qb)"
    else:
        board = build_board(projections, rules, byes=byes, top=args.top)
        title = f"{ref.name} - draft board (ADP: {adp_field(rules)})"

    slots = " ".join(f"{k}{v}" for k, v in rules.roster_slots.items())
    print(title)
    print(
        f"{rules.format}, {rules.num_teams} teams, {slots} | "
        f"rec {rules.scoring.get('rec', 0)} pass_td {rules.scoring.get('pass_td', 0)} | "
        f"waivers {rules.waiver_type} {rules.waiver_day or ''}"
    )
    for note in scoring_caveats(rules):
        print(f"note: {note}")
    print()
    print(render_table(board))
    return 0


def render_lineup(result: lineup_engine.LineupResult, rules: LeagueRules) -> str:
    """The lineup analysis as a fixed-width block, readable in a terminal."""
    rows = []
    for label, slot in zip(lineup_engine.slot_labels(result.optimal), result.optimal):
        player = slot.player
        rows.append({
            "slot": label,
            "player": player.name if player else "-",
            "pos": player.pos if player else "-",
            "team": (player.team or "-") if player else "-",
            "proj": round(slot.points, 1),
            "base": round(player.base_points, 1) if player else 0.0,
            "flags": ", ".join(player.flags) if player and player.flags else "",
        })

    lines = [render_table(pd.DataFrame(rows))]
    lines.append("")
    lines.append(
        f"projected: current {result.current_points:.1f} | "
        f"optimal {result.optimal_points:.1f} | delta {result.delta:+.1f}"
    )
    if result.moves:
        lines.append("")
        lines.append("lineup changes (target assignments, not platform click order):")
        lines.extend(f"  {move}" for move in result.moves)
    else:
        lines.append("the lineup already is the optimal one")

    bench = [p for p in result.bench if p.points > 0][:6]
    if bench:
        lines.append("")
        lines.append(
            "best bench: "
            + ", ".join(f"{p.name} {p.points:.1f}" for p in bench)
        )
    for note in result.notes:
        lines.append(f"note: {note}")
    for note in scoring_caveats(rules):
        lines.append(f"note: {note}")
    return "\n".join(lines)


def cmd_lineup(args: argparse.Namespace) -> int:
    keys = sorted(args.refs) if args.all else [args.league]
    args.week = args.week or current_week(args.season)
    if not keys or keys == [None]:
        print("lineup: pass --league <key> or --all", file=sys.stderr)
        return 2

    now = None if args.ignore_locks else lineup_engine.now_eastern()
    projections = sleeper.weekly_projections(args.season, args.week)
    if projections.empty:
        print(f"Sleeper has no week {args.week} projections for {args.season} yet.")
        return 1

    for key in keys:
        ref = args.refs[key]
        rules = roster.load_rules(ref, season=args.season)
        team = roster.load_roster(
            ref, args.week, season=args.season, rules=rules, projections=projections
        )
        print(f"{ref.name} - week {args.week} lineup")
        if not team.drafted:
            print("  no roster yet" + (f": {ref.note}" if ref.note else ""))
            print()
            continue
        result = lineup_engine.league_lineup(
            ref, args.week, season=args.season, now=now,
            projections=projections, rules=rules,
        )
        print(render_lineup(result, rules))
        print()
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    keys = args.leagues or list(args.refs)
    args.week = args.week or current_week(args.season)
    now = None if args.ignore_locks else lineup_engine.now_eastern()
    markdown, _ = brief_engine.weekly_brief(
        args.week, season=args.season, keys=keys, refs=args.refs, now=now
    )
    message = notify.Message(
        title=f"Fantasy week {args.week} brief",
        body=markdown,
        slug=f"brief_week{args.week}",
    )
    path = notify.FileNotifier().send(message)
    if args.slack:
        staged = notify.SlackNotifier().send(message)
        print(f"slack payload staged (not sent): {staged}", file=sys.stderr)
    print(message.markdown())
    print(f"written to {path}", file=sys.stderr)
    return 0


def cmd_refresh_fantasypros(args: argparse.Namespace) -> int:
    week = args.week or current_week(args.season, require_season_match=True)
    if not 1 <= week <= 18:
        raise ValueError("--week must be between 1 and 18")
    failed = False
    for scoring, position in fantasypros.PAGES:
        try:
            data = fantasypros.weekly_rankings(args.season, week, scoring=scoring,
                                              position=position, ttl=0)
            print(f"FantasyPros {args.season} week {week} {scoring}/{position}: "
                  f"{data['count']} players; source {data['published_at']}; "
                  f"retrieved {data['retrieved_at']}")
            if data.get("cache_saved") is False:
                failed = True
                print(data["cache_note"], file=sys.stderr)
        except (fantasypros.RankingsError, httpx.HTTPError, OSError) as exc:
            failed = True
            print(f"FantasyPros UNAVAILABLE {scoring}/{position}: "
                  f"{fantasypros.failure_reason(exc)}", file=sys.stderr)
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ff", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser("refresh-fantasypros", help="refresh dated weekly consensus, no league config needed")
    refresh.add_argument("--season", type=int, default=DEFAULT_SEASON)
    refresh.add_argument("--week", type=int, help="default: Sleeper's current week for this season")
    refresh.set_defaults(func=cmd_refresh_fantasypros)

    board = sub.add_parser("board", help="print a draft board for one league")
    board.add_argument("--league", required=True)
    board.add_argument("--top", type=int, default=200)
    board.add_argument("--season", type=int, default=None)
    board.add_argument(
        "--rookies", action="store_true", help="first-year players only (dynasty)"
    )
    board.set_defaults(func=cmd_board)

    line = sub.add_parser("lineup", help="optimal lineup for one league or all configured leagues")
    line.add_argument("--league", metavar="KEY")
    line.add_argument("--all", action="store_true", help="every league")
    line.add_argument(
        "--week", type=int, help="default: the week Sleeper says we are on"
    )
    line.add_argument("--season", type=int, default=None)
    line.add_argument(
        "--ignore-locks",
        action="store_true",
        help="reason about the week as if nothing had kicked off yet",
    )
    line.set_defaults(func=cmd_lineup)

    weekly = sub.add_parser("brief", help="the Tuesday brief across the configured leagues")
    weekly.add_argument(
        "--week", type=int, help="default: the week Sleeper says we are on"
    )
    weekly.add_argument("--season", type=int, default=None)
    weekly.add_argument(
        "--leagues", nargs="*", metavar="KEY", help="default: all configured leagues"
    )
    weekly.add_argument("--ignore-locks", action="store_true")
    weekly.add_argument(
        "--slack", action="store_true",
        help="also stage a Slack payload under data/out/slack/ (never sends)",
    )
    weekly.set_defaults(func=cmd_brief)
    for command in (board, line, weekly):
        command.add_argument("--config", help="local TOML file (default: FF_CONFIG or leagues.toml)")
    demo = sub.add_parser("demo", help="offline synthetic draft board and lineup, no accounts needed")
    demo.set_defaults(func=cmd_demo)
    return parser


def cmd_demo(args: argparse.Namespace) -> int:
    from ff.demo import run_demo
    print(run_demo())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "demo":
        return args.func(args)
    try:
        if args.command == "refresh-fantasypros":
            if not 2000 <= args.season <= 2100:
                raise ValueError("--season must be between 2000 and 2100")
            if args.week is not None and not 1 <= args.week <= 18:
                raise ValueError("--week must be between 1 and 18")
            return args.func(args)
        args.refs = load_leagues(args.config)
        if args.command == "lineup":
            if bool(args.league) == bool(args.all):
                raise ValueError("lineup requires exactly one of --league KEY or --all")
            keys = list(args.refs) if args.all else [args.league]
        elif args.command == "brief":
            keys = args.leagues or list(args.refs)
        else:
            keys = [args.league]
        unknown = set(keys) - args.refs.keys()
        if unknown:
            raise ValueError("unknown league(s): " + ", ".join(sorted(unknown)))
        seasons = {args.refs[key].season for key in keys}
        if args.season is None:
            if len(seasons) != 1:
                raise ValueError("selected leagues have different seasons; select one season's leagues")
            args.season = seasons.pop()
        if not 2000 <= args.season <= 2100:
            raise ValueError("--season must be between 2000 and 2100")
        if getattr(args, "week", None) is not None and not 1 <= args.week <= 18:
            raise ValueError("--week must be between 1 and 18")
        if getattr(args, "top", 1) < 1:
            raise ValueError("--top must be positive")
        args.refs = {k: replace(v, season=args.season) for k, v in args.refs.items()}
        return args.func(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    except httpx.HTTPError as error:
        # Do not echo request headers, cookies or private league URLs.
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
        print(f"Provider request failed ({status}). Check connectivity and provider access; ESPN credentials may have expired.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
