# Fantasy Football Toolkit

> **TL;DR:** A free, open-source Python CLI for Sleeper and ESPN draft boards, lineup optimization, and weekly briefs. Run the offline demo in minutes. Connect your own leagues when ready. All provider access is read-only.

[![Tests](https://github.com/jessecmaddox3/fantasy-football-toolkit/actions/workflows/tests.yml/badge.svg)](https://github.com/jessecmaddox3/fantasy-football-toolkit/actions/workflows/tests.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Built for managers who want calculations they can inspect and adapt to their league's scoring. Supports redraft, keeper and dynasty formats, including PPR, superflex and TE premiums. The CLI needs no AI subscription or model API key. Its plain-text output can also be used with an AI assistant.

## Try it without an account

Requires **Python 3.11+** and Git. This release installs directly from GitHub; it is not published on PyPI.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "git+https://github.com/jessecmaddox3/fantasy-football-toolkit.git@v0.1.0"
ff demo
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead of `source`. If your system calls Python `python3`, use that for the first command.

The demo uses fictional players and invented projections, performs no network requests, and writes no files. It shows a season draft board and a weekly lineup with concrete recommended swaps. [See the complete sample output](examples/demo-output.txt).

## What it does

- **Draft boards:** score supplied player projections under your league rules, rank by value over replacement, select format-appropriate ADP, and attach bye weeks.
- **Lineups:** solve starting slots and flex eligibility together, compare current and recommended starters, and account for kickoff locks, byes and unavailable players.
- **Weekly briefs:** combine standings, roster flags, upcoming byes, waiver settings, deadlines and lineup decisions across configured leagues. Sleeper also supports retrospective bench-point analysis.
- **Dynasty rookie boards:** select first-year players and optionally join DynastyProcess trade values. Its comparison columns currently use superflex values, even for a one-QB league.

This is an **alpha developer tool**. It does not submit waiver claims, trades, lineup changes or messages. You make changes in your fantasy platform yourself.

## Connect your own leagues

Download [examples/leagues.toml](examples/leagues.toml), save it as `leagues.toml` in your working directory, and replace the placeholder IDs. Remove any example leagues you do not use.

For Sleeper, use the numeric league ID from its web URL and your numeric user ID. You can look up a username with `https://api.sleeper.app/v1/user/YOUR_USERNAME`; use the returned `user_id`. No Sleeper API key is required. [Official API documentation](https://docs.sleeper.com/).

```toml
[leagues.home]
name = "My home league"
platform = "sleeper"
league_id = "YOUR_NUMERIC_LEAGUE_ID"
user_id = "YOUR_NUMERIC_USER_ID"
season = 2026
```

```bash
ff board --league home --top 25
ff lineup --league home --week 1
ff lineup --all --week 1
ff brief --week 1
```

Use `--config /path/to/leagues.toml` after the subcommand, or set `FF_CONFIG`. The config is validated before live requests. `--season` overrides the configured season; otherwise selected leagues must share a season. Lineup/brief default to the week reported by Sleeper. `--ignore-locks` is for hypothetical analysis before kickoff, not executable lineup advice.

For dynasty: `ff board --league dynasty --rookies --top 30` after adding a league with that key.

**ESPN:** configure `platform = "espn"`, your numeric `league_id`, `team_id` and `season`. Set `ESPN_S2` and `SWID` in your environment or a local `.env` file using cookies from your own ESPN session. Both cookies are currently required by this adapter, including for public leagues. `FF_ESPN_ENV` can select an explicit file. Environment values take precedence. Cookies can expire; ESPN is an unofficial integration and may change without notice.

Never put credentials in the TOML file or an issue report. Keep your local config, `.env`, cache and output private. Standard filenames are gitignored, but custom filenames need equivalent protection.

## Outputs and data sources

The defaults are relative to the directory where you run the command:

- `data/cache/`: downloaded provider responses; override with `FF_CACHE_DIR`.
- `data/out/brief_weekN.md`: weekly brief; override the directory with `FF_OUTPUT_DIR`.
- `ff brief --slack`: stages a local Slack JSON payload only. It does not send it.

Sleeper supplies league data and projections; nflverse supplies schedules; Open-Meteo supplies weather; ESPN supplies league data and scoreboard odds. Rookie comparisons optionally download DynastyProcess values. The code is MIT licensed; **provider data and service access have separate terms**, including noncommercial restrictions on some free APIs. No provider rankings or private league snapshots are bundled. See [data sources, attribution and terms](NOTICE.md).

## Limitations to understand

Projections are estimates. Value over replacement uses a heuristic allocation of league-wide flex demand; the weekly lineup assignment itself is exact for the supported slot model. Weather and implied-total adjustments are bounded heuristics, not validated predictions of improved fantasy results.

Kicker and defense projections can be incomplete, especially for ESPN scoring fields without a mapped equivalent. Unsupported scoring fields and missing player projections may undercount totals. IDP and unusual position types are not supported. Check your league rules and provider injury updates before acting. Cached information can be stale; use a fresh cache directory when checking a time-sensitive update.

Sleeper projections/ADP and ESPN endpoints are undocumented interfaces. External availability is not covered by the offline test suite. The release is verified with synthetic fixtures and a limited live Sleeper and ESPN board checks, not a guarantee that every league or future provider change works. Owner lookup currently expects the primary Sleeper owner ID. The tool is intended for local use by one manager, not a shared hosted service.

## Contribute

Useful contributions include reproducible bug reports, clearer setup instructions, missing scoring mappings backed by provider evidence, and new adapters. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](docs/ROADMAP.md).

```bash
git clone https://github.com/jessecmaddox3/fantasy-football-toolkit.git
cd fantasy-football-toolkit
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
python -m pytest tests
```

Tests use synthetic fixtures and block real network connections. AI-assisted contributions are welcome when the contributor understands the change, checks it, and explains how it was verified.

Maintained by [Jess Maddox](https://github.com/jessecmaddox3). Released under the [MIT license](LICENSE). Independently developed; not affiliated with or endorsed by Sleeper, ESPN, the NFL, or data providers.
