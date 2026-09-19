# A reusable AI-assisted workflow

> **TL;DR:** Let the toolkit calculate, ask an assistant to explain, and keep the final decision with you. AI is optional and does not run inside this app.

This is a starting workflow you can adapt in your own assistant or turn into a local skill. Use only output you are comfortable sharing with that assistant's provider. Local reports may include private league details; the fictional demo is a safe way to try the process.

## Draft preparation

Run a board with your league settings. Ask the assistant to explain the scoring assumptions, value-over-replacement tradeoffs and any missing-data warnings before discussing picks. ADP is a comparison input, not a promise that a player will be available. For rookie comparisons, keep the current superflex-value limitation explicit.

## Weekly review

Run a lineup and brief for an explicit season/week near the decision time. Check the source timestamps, injury flags, kickoff locks and any unavailable/unmatched consensus notes. Ask for an explanation of the final slot assignment, including retained-player moves through flex slots. Make changes yourself in the fantasy platform, then confirm the platform's actual lineup.

A reusable prompt:

```text
Explain this fantasy-football report using only the supplied output.
Separate the toolkit's calculations, source facts, and your judgment.
List the final starting slots and any bench/start/reposition steps.
Do not invent projections or replace unavailable consensus with memory.
Call out stale or missing data and custom-scoring limitations.
Do not access accounts, send messages, or change a roster.
Use fictional examples if you need to illustrate a rule.
```

## Improve the toolkit

When something looks wrong, isolate it into a tiny invented roster, rules object and expected outcome. Ask the assistant to reproduce the problem in a no-network test before proposing a change. Verify the test and installed demo yourself. Do not contribute a real league dump, cookies, strategy notes or an assistant transcript containing personal information.
