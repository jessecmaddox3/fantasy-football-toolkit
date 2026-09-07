# Contributing

> **TL;DR:** Pick a small issue, reproduce it with synthetic data, and submit a focused pull request with tests and an explanation.

Start with the [README setup](README.md#contribute), then run `python -m pytest tests`. The test suite blocks live network connections. Examples and fixtures should work without credentials, private files or your machine's paths.

## Changes that are especially useful

Report installation problems with your OS, Python version, command and redacted error. For scoring changes, show a minimal league rule, illustrative input, expected points, and an authoritative source for the field mapping. For an adapter, explain how a user obtains access, its terms, caching, failure behavior and how you tested it offline.

Keep pull requests focused. Include why the current behavior fails, what changes, and the command used to verify it. Avoid generated rewrites or new dependencies unrelated to the fix. The maintainer reviews changes and publishes releases; submitting a PR does not change installed copies automatically. There is no guaranteed response time.

## Contribution rules

Use only code and data you have permission to contribute. Preserve upstream notices. Contributions are accepted under this repository's MIT license unless a separately identified asset has an explicitly compatible license. Never submit API credentials, cookie values, real league payloads or private strategy notes. AI assistance is welcome: describe material use and personally verify the result. Authors remain responsible for correctness and provenance.

Treat others respectfully, explain disagreements with evidence, and avoid harassment or personal attacks. The maintainer may close abusive or out-of-scope discussions.

## Architecture

`ff/config.py` loads local league references. `ff/leagues.py` normalizes provider rules. `ff/sources/` handles GET-only integrations and `ff/cache.py` stores responses. `ff/engine/value.py`, `lineup.py`, and `brief.py` implement calculations and summaries. `ff/cli.py` is the entry point; `ff/demo.py` is a fully offline example.

The CLI's supported commands are the initial public interface. Internal Python APIs may change during the alpha period. Keep provider actions read-only. `ff/notify.py` stages Slack output but never delivers it.
