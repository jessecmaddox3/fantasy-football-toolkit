import pytest

from ff.cli import render_lineup
from ff.engine import lineup
from tests.test_lineup import player, rules, solve


def test_independent_position_upgrades_never_cross_pair():
    result = solve([player("Old Quarterback", "QB", 10), player("Old Runner", "RB", 20),
                    player("New Quarterback", "QB", 30), player("New Runner", "RB", 40)],
                   {"QB": 1, "RB": 1}, starters=["Old Quarterback", "Old Runner"])
    assert result.delta == 40
    assert {(s.slot, s.start.pos, s.sit.pos, s.gain) for s in result.swaps} == {
        ("QB", "QB", "QB", 20), ("RB", "RB", "RB", 20)}


def test_flex_chain_explains_retained_receiver_move():
    result = solve([player("Avery Receiver", "WR", 10), player("Blair Receiver", "WR", 20),
                    player("Casey Runner", "RB", 30)], {"WR": 1, "FLEX": 1},
                   starters=["Avery Receiver", "Blair Receiver"])
    assert result.delta == 20
    assert [(m.action, m.player.name, m.slot) for m in result.moves] == [
        ("bench", "Avery Receiver", "WR"),
        ("move", "Blair Receiver", "WR"),
        ("start", "Casey Runner", "FLEX"),
    ]
    rendered = render_lineup(result, rules({"WR": 1, "FLEX": 1}))
    assert "move Blair Receiver" in rendered and "FLEX to WR" in rendered
    assert "start Casey Runner" in rendered and "in FLEX" in rendered
    assert "delta +20.0" in rendered
    assert "over Avery Receiver" not in rendered


def test_repeated_slots_and_retained_moves_into_empty_slots_are_distinct():
    a = lineup.PlayerLine("a", "Avery Runner", "RB", points=20)
    b = lineup.PlayerLine("b", "Blair Runner", "RB", points=30)
    c = lineup.PlayerLine("c", "Casey Receiver", "WR", points=10)
    current = (lineup.Slot("RB"), lineup.Slot("RB", a), lineup.Slot("FLEX", c))
    optimal = (lineup.Slot("RB", a), lineup.Slot("RB", b), lineup.Slot("FLEX", c))
    moves = lineup.lineup_changes(current, optimal)
    assert [(m.action, m.player.player_id, m.slot, m.from_slot) for m in moves] == [
        ("move", "a", "RB #1", "RB #2"), ("start", "b", "RB #2", None)]
    swap, = lineup._swaps(current, optimal)
    assert swap.sit is None and swap.gain == 30


def test_reposition_only_cycle_is_not_called_already_optimal():
    a = lineup.PlayerLine("a", "Avery Runner", "RB", points=20)
    b = lineup.PlayerLine("b", "Blair Runner", "RB", points=30)
    result = lineup.LineupResult("test", 1,
        (lineup.Slot("RB", b), lineup.Slot("FLEX", a)),
        (lineup.Slot("RB", a), lineup.Slot("FLEX", b)), (), (), (a, b))
    assert len(result.moves) == 2
    assert result.delta == 0
    assert "already is the optimal" not in render_lineup(result, rules({"RB": 1, "FLEX": 1}))


@pytest.mark.parametrize("slots", [{"QB": 1, "RB": 2, "SUPER_FLEX": 1},
                                    {"WR": 1, "REC_FLEX": 1, "FLEX": 1}])
def test_change_destinations_equal_exact_legal_final_assignment(slots):
    rows = [player("A", "QB", 20), player("B", "RB", 30), player("C", "RB", 10),
            player("D", "WR", 25), player("E", "TE", 21)]
    result = solve(rows, slots)
    final = {i: s.player.player_id for i, s in enumerate(result.optimal) if s.player}
    assert {m.slot_index: m.player.player_id for m in result.moves if m.action == "start"} == final
    assert len(set(final.values())) == len(final)


def test_locked_players_never_receive_moves():
    import datetime as dt
    result = solve([player("Locked", "RB", 10, team="A"), player("Free", "RB", 30, team="B")],
                   {"RB": 1}, starters=["Locked"],
                   now=dt.datetime(2030, 9, 1, 14), kickoffs={"A": dt.datetime(2030, 9, 1, 13)})
    assert result.moves == ()


def test_brief_keeps_the_flex_reposition_in_actionable_guidance():
    from ff.engine.brief import decisions_for
    from tests.test_brief import _drafted_team
    result = solve([player("Avery Receiver", "WR", 10), player("Blair Receiver", "WR", 20),
                    player("Casey Runner", "RB", 30)], {"WR": 1, "FLEX": 1},
                   starters=["Avery Receiver", "Blair Receiver"])
    text = "\n".join(decisions_for(rules({"WR": 1, "FLEX": 1}), _drafted_team(), result, {}, 1))
    assert "move Blair Receiver" in text and "FLEX to WR" in text
    assert "start Casey Runner" in text and "in FLEX" in text
    assert "+20.0 projected points" in text


def test_brief_final_table_distinguishes_repeated_slot_destinations():
    from ff.engine.brief import LeagueBrief, render_league
    from ff.leagues import LeagueRef
    from tests.test_brief import _drafted_team
    result=solve([player('Avery Runner','RB',20),player('Blair Runner','RB',30)], {'RB':2})
    section=render_league(LeagueBrief(ref=LeagueRef('demo','Demo','sleeper','100001'),
        rules=rules({'RB':2}),team=_drafted_team(),lineup=result))
    for label,slot in zip(('RB #1','RB #2'),result.optimal):
        assert f'| {label} | {slot.player.name} |' in section
