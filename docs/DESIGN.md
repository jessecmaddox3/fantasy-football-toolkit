# How the toolkit fits together

> **TL;DR:** Keep league rules separate from provider data, score locally, solve all starting slots together, and explain the final assignment. External consensus adds context without taking over the calculation.

I built the original for my own fantasy workflow. Different league settings make a single generic ranking less useful, so the toolkit starts with the league's scoring and roster structure. The public version keeps those choices and replaces private league details with configurable inputs and constructed examples.

## Rules first

`ff/config.py` validates local league references before any request. `ff/leagues.py` normalizes Sleeper and ESPN settings into one model. Credentials belong in the local environment, separately from shareable configuration examples. The roster layer preserves current starters, reserve/taxi status and provider identity.

`ff/engine/value.py` maps supplied statistics to fantasy points. Draft boards add value over replacement and format-aware ADP. Replacement levels allocate league-wide flex demand heuristically; they are an aid to comparison, not a simulated draft or a guarantee of player value.

## One lineup problem

`ff/engine/lineup.py` assigns eligible players to all supported starting slots together. It preserves kickoff locks and excludes inactive reserve/taxi players. Weather and implied-total adjustments are bounded heuristics layered on the underlying projection. Missing projections, unsupported scoring and availability flags remain visible.

The optimizer's final assignment is exact for its supported slot model and supplied scores. That does not make its input projections certain. Superflex, repeated slots and receiver-only flex can require moving an existing starter before adding a new one, so explanations identify every bench, reposition and start by slot index. The CLI and brief show target assignments and the total projected difference. They do not pretend to know each platform's click order.

## A reference beside the model

`ff/sources/fantasypros.py` reads weekly consensus from fixed public ranking pages. Season, week, scoring, position, row shape, counts and timestamps are checked before use. It refreshes after six hours and rejects source updates older than 48 hours. Provider publication time and actual retrieval time stay separate, even on a cache hit. Expired data is not a fallback when refresh fails.

Player matching requires an unambiguous normalized name/team pair; defenses use an unambiguous canonical team. A missing or duplicate identity becomes an unmatched note. PPR, half-PPR and standard are reference buckets, not substitutes for custom scoring. Consensus never changes the selected lineup or projected points.

## Small replaceable pieces

The module map follows the flow from input to output. Each provider can be tested with an invented HTTP transport independently of the scoring engine.

| Piece | Responsibility |
| --- | --- |
| `ff/config.py`, `ff/leagues.py`, `ff/roster.py` | Configuration, league rules, roster identity and availability |
| `ff/sources/` | GET-only provider adapters and source-specific interpretation |
| `ff/cache.py` | Scoped cache files and atomic owner-only writes on POSIX |
| `ff/engine/` | Scoring, draft value, lineup assignment and weekly decisions |
| `ff/cli.py`, `ff/notify.py` | Terminal output, Markdown reports and local Slack payload staging |
| `ff/demo.py`, `tests/` | Fictional offline demonstration and synthetic regression checks |

## Boundaries I chose

This is a local tool for one manager. It does not host accounts, automate transactions, submit lineup changes, post messages or promise better results. Keeping provider access read-only makes it easier to inspect a suggestion before acting. Provider terms and data licenses remain separate from the MIT code license.

The test suite blocks real sockets and uses invented data. CI checks Linux and Windows, and the wheel is exercised outside the checkout. That catches packaging mistakes and accidental source-directory imports. It cannot establish that every live league or tomorrow's provider response will work.
