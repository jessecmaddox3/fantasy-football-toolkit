import io
import os
from pathlib import Path

import pytest
import json

from ff import notify

BODY = """## Week 1

**Example League** 0-0

- start Aaron Rodgers over Courtland Sutton (+3.2)
- CMC is Questionable
"""

MESSAGE = notify.Message(title="Week 1 brief", body=BODY, slug="brief_week1")


# --------------------------------------------------------------- formatting

def test_slack_bold_is_one_asterisk_not_two():
    got = notify.to_slack_mrkdwn("**Example League** 0-0")
    assert got == "*Example League* 0-0"


def test_slack_has_no_headings_so_they_become_bold_lines():
    got = notify.to_slack_mrkdwn("## Week 1\n\ntext")
    assert got.splitlines()[0] == "*Week 1*"


def test_slack_bullets_become_real_bullet_characters():
    got = notify.to_slack_mrkdwn("- start Rodgers\n  - nested")
    assert got.splitlines() == ["• start Rodgers", "  • nested"]


def test_a_payload_leads_with_a_header_block_carrying_the_title():
    payload = notify.slack_payload(MESSAGE)
    assert payload["text"] == "Week 1 brief"
    assert payload["blocks"][0]["type"] == "header"
    assert payload["blocks"][0]["text"]["text"] == "Week 1 brief"


def test_a_payload_body_is_mrkdwn_sections():
    payload = notify.slack_payload(MESSAGE)
    sections = [b for b in payload["blocks"] if b["type"] == "section"]
    assert sections
    assert all(b["text"]["type"] == "mrkdwn" for b in sections)
    assert "*Example League*" in sections[0]["text"]["text"]


def test_a_long_body_is_split_so_no_block_exceeds_slacks_limit():
    long_body = "\n\n".join(["paragraph " * 60] * 20)
    payload = notify.slack_payload(notify.Message("t", long_body))
    sections = [b for b in payload["blocks"] if b["type"] == "section"]
    assert len(sections) > 1
    assert all(len(b["text"]["text"]) <= notify.SLACK_BLOCK_LIMIT for b in sections)


def test_a_single_unbroken_paragraph_is_still_split():
    payload = notify.slack_payload(notify.Message("t", "x" * 9000))
    sections = [b for b in payload["blocks"] if b["type"] == "section"]
    assert all(len(b["text"]["text"]) <= notify.SLACK_BLOCK_LIMIT for b in sections)


def test_a_channel_is_only_set_when_one_is_configured():
    assert "channel" not in notify.slack_payload(MESSAGE)
    assert notify.slack_payload(MESSAGE, "C123")["channel"] == "C123"


# ---------------------------------------------------------------- notifiers

def test_the_console_notifier_prints_the_markdown_body():
    stream = io.StringIO()
    notify.ConsoleNotifier(stream=stream).send(MESSAGE)
    text = stream.getvalue()
    assert text.startswith("# Week 1 brief")
    assert "Aaron Rodgers" in text


def test_the_file_notifier_writes_markdown_and_returns_the_path(tmp_path):
    path = notify.FileNotifier(directory=tmp_path).send(MESSAGE)
    assert path.endswith("brief_week1.md")
    assert "Aaron Rodgers" in (tmp_path / "brief_week1.md").read_text()


def test_a_title_with_no_slug_still_produces_a_filename(tmp_path):
    path = notify.FileNotifier(directory=tmp_path).send(
        notify.Message("Week 1: Example League!", "body")
    )
    assert path.endswith("week-1-example-league.md")


def test_the_slack_notifier_stages_the_payload_and_sends_nothing(tmp_path):
    path = notify.SlackNotifier(channel="C123", directory=tmp_path).send(MESSAGE)

    payload = json.loads((tmp_path / "brief_week1.json").read_text())
    assert path.endswith("brief_week1.json")
    assert payload["channel"] == "C123"
    assert payload["blocks"][0]["type"] == "header"


def test_turning_off_the_dry_run_refuses_rather_than_guessing_a_channel(tmp_path):
    import pytest

    with pytest.raises(NotImplementedError):
        notify.SlackNotifier(directory=tmp_path, dry_run=False).send(MESSAGE)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner permissions")
@pytest.mark.parametrize("notifier,suffix", [(notify.FileNotifier, ".md"), (notify.SlackNotifier, ".json")])
def test_reports_are_private_even_when_replacing_a_public_file(tmp_path, notifier, suffix):
    path = tmp_path / (MESSAGE.filename + suffix)
    path.write_text("old")
    path.chmod(0o644)
    notifier(directory=tmp_path).send(MESSAGE)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("notifier,suffix", [(notify.FileNotifier, ".md"), (notify.SlackNotifier, ".json")])
def test_failed_report_replacement_preserves_previous_copy(tmp_path, monkeypatch, notifier, suffix):
    path = tmp_path / (MESSAGE.filename + suffix)
    path.write_text("previous report")
    def failed_replace(self, target):
        raise OSError("synthetic full disk")
    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(OSError, match="full disk"):
        notifier(directory=tmp_path).send(MESSAGE)
    assert path.read_text() == "previous report"
    assert list(tmp_path.iterdir()) == [path]
