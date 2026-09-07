"""A deterministic, offline demonstration using entirely fictional players."""
import pandas as pd
from ff.leagues import normalize_league
from ff.engine.lineup import optimal_lineup


def run_demo() -> str:
    from ff.cli import build_board, render_table, render_lineup
    rules = normalize_league({
        "name": "Demo League", "league_id": "demo", "total_rosters": 2,
        "settings": {"type": 0, "waiver_type": 2, "waiver_budget": 100},
        "roster_positions": ["QB", "RB", "WR", "FLEX", "BN", "BN"],
        "scoring_settings": {"pass_yd": .04, "pass_td": 4., "rush_yd": .1,
                             "rush_td": 6., "rec": 1., "rec_yd": .1, "rec_td": 6.},
    })
    rows = [
        ("q1", "Alex Quarterback", "QB", "BUF", {"pass_yd": 250, "pass_td": 2}),
        ("q2", "Blair Quarterback", "QB", "KC", {"pass_yd": 200, "pass_td": 1}),
        ("r1", "Casey Runner", "RB", "DET", {"rush_yd": 80, "rush_td": 1, "rec": 2}),
        ("r2", "Drew Runner", "RB", "ATL", {"rush_yd": 50, "rec": 1}),
        ("r3", "Elliot Runner", "RB", "GB", {"rush_yd": 30}),
        ("w1", "Frankie Receiver", "WR", "CIN", {"rec": 7, "rec_yd": 90, "rec_td": 1}),
        ("w2", "Gray Receiver", "WR", "SEA", {"rec": 4, "rec_yd": 50}),
        ("w3", "Harper Receiver", "WR", "NYG", {"rec": 2, "rec_yd": 20}),
        ("t1", "Indigo Tight End", "TE", "LV", {"rec": 5, "rec_yd": 60}),
    ]
    weekly = pd.DataFrame([dict(player_id=pid, name=name, pos=pos, team=team,
                               adp_ppr=i+1., **stats)
                           for i, (pid, name, pos, team, stats) in enumerate(rows)])
    season = weekly.copy()
    for stat in rules.scoring:
        if stat in season:
            season[stat] = season[stat] * 17
    board = build_board(season, rules, byes={"BUF": 7, "DET": 6, "CIN": 8})
    result = optimal_lineup(["q1", "r1", "w1", "w2", "w3", "t1"], 1, rules,
                            projections=weekly, starters=["q1", "r1", "w3", "w2"])
    return ("Fantasy Football Toolkit: offline demo\n"
            "All players, projections and league settings below are synthetic.\n"
            "No accounts, credentials, network requests or AI subscription required.\n\n"
            "DRAFT BOARD (season projections)\n" + render_table(board) +
            "\n\nLINEUP (weekly projections)\n" + render_lineup(result, rules))
