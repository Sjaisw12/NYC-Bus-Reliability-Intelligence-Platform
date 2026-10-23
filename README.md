# Project 3 — NYC Bus Reliability Intelligence Platform

Phase 0 only. This repository contains an offline-testable, one-route, single-poll
collector. No collection service is running. B63 / `MTA NYCT_B63` is a documented
candidate, not a verified current API/GTFS mapping.

Python 3.12+; runtime and tests use only the standard library. No third-party runtime
or test dependencies need installing or locking. Run from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m mta_collector.cli --fixture tests/fixtures/vehicle.json --output data/fixture
```

The fixture is synthetic, not a real MTA response. The CLI prints a health summary
followed by persisted normalized JSON rows. Accepted observations and quarantined
records are stored separately under data/.../derived/, linked to immutable raw
bundles; recovery is idempotent. Output roots must be below repository data/ or
/tmp/ (disposable tests); other paths and existing symlinks are rejected. All fixture bundles are labeled `mode=fixture`.
Do not interpret present-day freshness flags on this old fixture as live health.

Live access is disabled by default. It requires BOTH `--live` and
`MTA_COLLECTOR_ENABLE_LIVE=1`, plus `MTA_BUSTIME_API_KEY` supplied by the secure
runtime. Do not enable these without separate authorization. The CLI has no
continuous-loop mode. `config/pilot.toml` records the proposed settings for review;
it is advisory, not a live-mode switch or a runtime configuration loader.

See [four-correction review](docs/phase-0-corrections.md) and [implementation evidence](docs/phase-0-report.md),
[data contract](docs/data-contract.md), and [operations](docs/operations.md).
