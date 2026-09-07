from ff.sources import dynastyprocess as dp
from tests.conftest import RecordingRoutes, fixture_text

VALUES = fixture_text("dynastyprocess_values.csv")


def routes():
    return RecordingRoutes({"values.csv": VALUES})


def test_values_returns_the_documented_columns_typed():
    df = dp.values(client=routes().client())
    assert {"player", "pos", "team", "value_1qb", "value_2qb", "ecr_2qb"} <= set(df.columns)
    assert df["value_2qb"].dtype.kind == "f"
    chase = df.set_index("player").loc["Ja'Marr Chase"]
    assert chase["value_2qb"] == 9500
    assert chase["pos"] == "WR"


def test_values_adds_a_normalized_join_key():
    df = dp.values(client=routes().client()).set_index("player")
    assert df.loc["Ja'Marr Chase", "merge_name"] == "jamarr chase"


def test_normalize_name_strips_punctuation_suffixes_and_case():
    assert dp.normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert dp.normalize_name("Marvin Harrison Jr.") == "marvin harrison"
    assert dp.normalize_name("Michael Pittman Jr") == "michael pittman"
    assert dp.normalize_name("A.J. Brown") == "aj brown"
    assert dp.normalize_name("  Kenneth  Walker III ") == "kenneth walker"
    assert dp.normalize_name(None) == ""


def test_2qb_ordering_is_usable_as_a_superflex_board():
    df = dp.values(client=routes().client()).sort_values("value_2qb", ascending=False)
    assert df.iloc[0]["value_2qb"] >= df.iloc[1]["value_2qb"]
