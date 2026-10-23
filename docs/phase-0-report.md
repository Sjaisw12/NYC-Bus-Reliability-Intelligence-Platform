# Phase 0 offline implementation evidence

Review date: October 22, 2026 America/New_York (execution crossed October 23 UTC).
Status: offline implementation tested; current official GTFS verification blocked;
Gate 0 NOT passed; no live sample; no deployment.

## Repository and environment

Verified checkout: `/workspace/NYC-Bus-Reliability-Intelligence-Platform`
Origin: `https://github.com/Sjaisw12/NYC-Bus-Reliability-Intelligence-Platform.git`
Branch: `work`; no commits exist. No applicable AGENTS.md was found.
All files listed below are new, uncommitted files. No push, PR, merge, or deployment.

Used cloud-environment-runtime skill to inspect managed readiness without secret
values. Two status checks returned current observations for configuration revision
3: network mode restricted, state enforced, no custom allowed hosts, and no secret
bindings. Local shell reported MTA_BUSTIME_API_KEY absent/empty using a presence-only
check. /etc/codex/network-policy.json likewise lacked the MTA hosts. This differs
from the user's intended configuration; it does not establish that their saved
configuration is absent elsewhere. No configuration changes or bypasses attempted.

Unauthenticated static download attempted:

```sh
curl --fail --silent --show-error --location --max-time 60 https://rrgtfsfeeds.s3.amazonaws.com/gtfs_b.zip --output /tmp/project3-brooklyn.zip
```

Exit 7: `Failed to connect to proxy port 8080 ... Could not connect to server`.
No official ZIP was obtained. Current B63 agency/route IDs, directions, active service
dates, trip/stop/shape joins, and ZIP SHA-256 are all PENDING. No fabricated digest
or synthetic-feed result is offered as official evidence. MTA NYCT_B63 is only the
documented candidate. The source feed URL came from the official MTA developer index
researched in the preceding proposal.

## New files

- `.gitignore`, `.env.example`: ignore credentials/runtime data; empty placeholder.
- `pyproject.toml`: Python 3.12+, zero third-party runtime/test dependencies.
- `README.md`: offline setup and live guard explanation.
- `config/pilot.toml`: advisory candidate settings, live disabled.
- `src/mta_collector/__init__.py`: side-effect-free package.
- `src/mta_collector/core.py`: transport, parser, timestamp separation, redaction,
  immutable bundle writes, bounded retry/failure records, recoverable health counts.
- `src/mta_collector/cli.py`: fixture mode and separately guarded single-poll live mode.
- `src/mta_collector/gtfs.py`: offline official-ZIP inspector, ready for an actual ZIP.
- `tests/test_collector.py`, `tests/test_gtfs_cli.py`: deterministic offline suite.
- `tests/fixtures/vehicle.json`: explicitly synthetic SIRI-shaped response.
- `docs/data-contract.md`, `docs/operations.md`, `docs/phase-0-report.md`:
  contracts, limitations, operations, and this evidence packet.

## Verification executed

`PYTHONPATH=src python -m unittest discover -s tests -v`: exit 0; 18 tests passed.
Every test blocks socket creation. Clocks and retry sleeps are injected; no test
uses the configured MTA credential or an authenticated endpoint. Coverage includes
malformed envelopes/rows, stale/future timestamps, empty success, provider errors,
timeouts, 429/503 retry bounds, 401/403 stop, Retry-After, duplicate retention across
restart, corrupted hashes, before/after-commit crash injection, no overwrite,
secret echoes in body/envelope, CLI live guards, and synthetic GTFS schedule
exceptions, directions, agency/stop/shape linkage. Synthetic GTFS accepts >24h stop
times without converting them into incorrect UTC dates; full service-time conversion
and DST journey identity are deferred (no journey inference exists here).

`PYTHONPATH=src python -m compileall -q src tests`: exit 0.

`PYTHONPATH=src python -m mta_collector.cli --fixture tests/fixtures/vehicle.json --output data/offline-evidence`:
exit 0. One persisted poll, one synthetic row, zero failures, zero duplicates,
zero quarantines, one stale row. Raw bytes: 473. Source recorded time:
2026-10-22T11:59:30Z. Ingest: 2026-10-23T00:23:44.152710Z. This is fixture evidence,
not service uptime or a real MTA observation. Output bundle:
`data/offline-evidence/poll-8b6bbc29de124c9ebbd35034a3db2a63/` (ignored by Git).

No CI was created or run. No authenticated live request was made. Standard-library
unittest means offline testing needs no dependency download. Package installation
was not required or tested; commands use PYTHONPATH=src.

## Review boundaries and remaining gates

1. Apply/attach the intended domain and secret configuration to this runtime and
   verify observed readiness. Do not infer readiness from this code.
2. Download the actual official ZIP; record retrieval metadata/hash/effective
   calendars and verify the B63 mapping. The offline inspector is not that evidence.
3. Obtain separate authorization for a narrow live smoke test. Inspect real JSON v2
   fields, exact LineRef semantics, optional fields, and timestamp fidelity before
   treating the provisional parser as confirmed against MTA.
4. Persist at least one real observation, inspect secret-safe failure/output paths,
   and review restart evidence with the actual storage host before Gate 0 approval.
5. Continuous collection needs a separate host, persistence, retention, disk/budget,
   liveness monitoring, schedule/coverage, and approval decision. None is provisioned.

Raw bundles are immutable by application policy, not WORM. Process-crash injection
is not a physical power-loss test. No GTFS join, Parquet pipeline, independent
heartbeat daemon, daily report scheduler, trip reconstruction, dashboard, or
reliability analysis is implemented. Health is a post-run snapshot of persisted
attempts; it does not claim complete route-minute coverage. See operations for
storage and retry details. No future phase was started.
