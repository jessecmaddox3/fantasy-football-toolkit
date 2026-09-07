# Code and data provenance

> **TL;DR:** MIT covers this project's code and constructed examples. It does not relicense downloaded data, grant API access rights, or imply provider endorsement.

The toolkit grew from personal fantasy-management software developed with AI assistance. The public release contains reusable Python source and tests, with a new configuration layer. Its fixtures were constructed for this release; no private league snapshots, purchased rankings, provider projections or upstream datasets are bundled. Public athlete names in tests exercise name matching; their example projections and ranks are invented and must not be treated as factual predictions. The demo uses entirely fictional players.

## Runtime sources

The adapters retrieve data only when a live command requests it. Review the applicable terms before redistribution or commercial use. These links were checked for the initial release in September 2026; terms can change.

- **Sleeper:** [documented API](https://docs.sleeper.com/) is read-only and states free noncommercial use; commercial use requires a licensing conversation. The projection and ADP endpoints at `api.sleeper.com` are undocumented. Do not assume the documented API permission grants rights to repackage third-party projections (commonly labeled Rotowire).
- **ESPN:** league and scoreboard read endpoints are unofficial integrations. This project offers no ESPN data license or commercial permission. Cookies authenticate your own account and must remain local.
- **nflverse / nfldata:** [upstream repository](https://github.com/nflverse/nfldata), [nflverse data repository and license](https://github.com/nflverse/nflverse-data). Schedules are retrieved from upstream at runtime. Check dataset-specific provenance and attribution before sharing derived outputs.
- **Open-Meteo:** [terms](https://open-meteo.com/en/terms) and [data license](https://open-meteo.com/en/license). The free service is for noncommercial use with request limits; weather data carries attribution obligations under CC BY 4.0. Credit Open-Meteo when sharing weather-derived outputs.
- **DynastyProcess:** [data repository](https://github.com/dynastyprocess/data) and its [GPL-3.0 license](https://github.com/dynastyprocess/data/blob/master/LICENSE). The optional rookie comparison downloads `values.csv`. That data remains governed by upstream terms; it is not part of this MIT distribution.

## Local information

Live provider responses, reports and configurations may contain manager names, league IDs and other personal information. They are private working files, not contribution fixtures. Create minimal synthetic reproductions for issues and tests. Removing a file from the current tree does not remove it from Git history.
