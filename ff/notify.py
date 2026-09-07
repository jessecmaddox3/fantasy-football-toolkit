"""Pluggable delivery for briefs and lineup alerts.

**Nothing in this module sends anything anywhere.**  :class:`SlackNotifier`
formats a Slack message and writes the exact payload it *would* post to
``data/out/slack/``; wiring it to a real channel is a separate, deliberate step. That keeps the write path off by
construction while the formatting is developed and reviewed.

Three notifiers, one interface:

* :class:`ConsoleNotifier` -- prints, for a terminal run.
* :class:`FileNotifier` -- writes Markdown under ``data/out/``, which is what
  scheduled jobs or other local tools read.
* :class:`SlackNotifier` -- renders Slack ``mrkdwn`` and stages the payload.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

def output_dir() -> Path:
    return Path(os.environ.get("FF_OUTPUT_DIR", Path.cwd() / "data" / "out"))

#: Slack refuses a section block longer than this many characters.
SLACK_BLOCK_LIMIT = 2900

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG.sub("-", text.strip().lower()).strip("-") or "message"


@dataclass(frozen=True)
class Message:
    """One thing to deliver: a title, a Markdown body, and where it came from."""

    title: str
    body: str
    slug: str = ""
    league: str | None = None

    @property
    def filename(self) -> str:
        return f"{self.slug or slugify(self.title)}"

    def markdown(self) -> str:
        return f"# {self.title}\n\n{self.body.strip()}\n"


# --------------------------------------------------------------- formatting

def to_slack_mrkdwn(markdown: str) -> str:
    """Markdown -> Slack ``mrkdwn``.

    Slack is not Markdown: bold is one asterisk, headings do not exist, and
    bullets render better as real bullet characters.
    """
    lines: list[str] = []
    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            lines.append(f"*{heading.group(2).strip()}*")
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        lines.append(line)
    return "\n".join(lines).strip()


def _chunk(text: str, limit: int = SLACK_BLOCK_LIMIT) -> list[str]:
    """Split on paragraph boundaries so no block exceeds Slack's limit."""
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(para[:cut])
            para = para[cut:].lstrip("\n")
        current = para
    if current:
        chunks.append(current)
    return chunks


def slack_payload(message: Message, channel: str | None = None) -> dict:
    """The Block Kit payload for ``message``, ready to post -- but not posted."""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": message.title[:150], "emoji": True},
        }
    ]
    for chunk in _chunk(to_slack_mrkdwn(message.body)):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    payload = {"text": message.title, "blocks": blocks}
    if channel:
        payload["channel"] = channel
    return payload


# ---------------------------------------------------------------- notifiers

@dataclass
class ConsoleNotifier:
    """Prints the Markdown body.  The fallback for an interactive run."""

    stream: TextIO = field(default_factory=lambda: sys.stdout)

    def send(self, message: Message) -> str:
        self.stream.write(message.markdown())
        return "console"


@dataclass
class FileNotifier:
    """Writes Markdown under ``data/out/``.  The default file output."""

    directory: Path = field(default_factory=output_dir)
    suffix: str = ".md"

    def send(self, message: Message) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{message.filename}{self.suffix}"
        path.write_text(message.markdown())
        return str(path)


@dataclass
class SlackNotifier:
    """Formats a Slack message and **stages** it; it never posts.

    Delivery is intentionally not implemented. Output can be inspected locally.
    """

    channel: str | None = None
    directory: Path = field(default_factory=lambda: output_dir() / "slack")
    dry_run: bool = True

    def send(self, message: Message) -> str:
        payload = slack_payload(message, self.channel)
        if not self.dry_run:  # pragma: no cover - deliberately unreachable
            raise NotImplementedError(
                "Slack delivery is not wired up; use the staged payload"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{message.filename}.json"
        path.write_text(json.dumps(payload, indent=2))
        return str(path)
