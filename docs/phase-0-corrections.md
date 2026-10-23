# Phase 0 — four corrections for independent review

Scope: derived persistence, network-secret query construction, artifact safety,
and poll/attempt accounting only. No authenticated requests, scheduler, GTFS
reconstruction, analytical database, dashboard, deployment, Git commit, push, PR,
merge, or branch change. Gate 0 remains NOT PASSED.

## Implementation changes

- core.py: raw storage layout retained; separate atomic derived directories hold
  accepted.jsonl and quarantine.jsonl. Stable attempt/ordinal IDs and source lineage
  are persisted. Recovery completes missing views, verifies existing views, never
  appends duplicates, and does not edit raw bundles. Legacy raw files are supported.
- core.py: URL-safe opaque credentials are included unchanged; unsupported formats
  fail closed before transport. Other query parameters are encoded independently.
  Conservative credential-field/UUID echo rejection supplements local-placeholder
  matching. No actual key or placeholder was used for compatibility tests.
- core.py / cli.py / .gitignore: storage constrained to repository data/ descendants
  or disposable /tmp descendants, rejecting symlinks and traversal to other roots.
  CLI validates output before transport construction. Added broad artifact/credential
  ignores without hiding sanitized docs, the example, or synthetic fixtures.
- core.py / cli.py: retries share one poll ID and have unique attempt IDs. Health
  separates attempts, successful/failed logical polls, and incomplete polls. A retry
  that succeeds returns CLI success; errors from earlier attempts remain counted.
- tests/test_collector.py: retained all original test cases; corrected the transport
  exception assertion from three failed polls to three failed attempts and one
  failed poll. This is the intentional accounting behavior change.
- tests/test_corrections.py: 15 focused regression tests, all with sockets blocked.
- README.md, data-contract.md, operations.md: document storage, compatibility,
  health semantics, recovery, and remaining limits. Original phase-0-report.md stays
  unchanged as historical evidence.

## Exact offline test command and result

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Exit 0: Ran 33 tests in 0.128s; OK. Original 18 cases plus 15 new cases pass.
No package download or installation was required. No actual MTA credential or
placeholder was used to exercise LiveTransport; its outgoing requests are mocked.

Security/storage regressions cover: accepted/quarantine separation and lineage,
legacy recovery, crashes at raw/derived commit boundaries, repeated recovery,
derived corruption, failed-then-successful retry counts and CLI exit, interrupted
retry counts, synthetic placeholder preservation/rejection, exception redaction,
unknown raw credential-shaped echo rejection, root/traversal/symlink constraints,
output validation before transport, and git-ignore coverage with fixture/doc visibility.

## Remaining limitations

- Actual injected placeholder format and Codex query-parameter substitution cannot
  be confirmed offline. Unsupported characters are rejected with no credential in
  the error; do not disable that guard as a workaround. The authorized live smoke
  test remains a separate review gate.
- Credential-echo detection is conservative and can reject legitimate UUID-bearing
  payloads; it cannot detect every encoding or secret format. Raw data is private,
  ignored, and unsuitable for automatic public release.
- Single writer and trusted POSIX filesystem only; not WORM, not resistant to a
  concurrent malicious filesystem writer, and not proof of host power-loss safety.
  Committed mismatched derived views fail closed; repair requires explicit review.
- /tmp is for disposable tests, not durable production. Do not target another Git
  checkout there. Ignore rules cannot prevent forced additions or secrets hidden
  in arbitrary source filenames.
- Historical attempts without logical linkage cannot be retroactively grouped.
  Unpersisted in-flight attempts remain unknown. Interrupted logical polls are
  reported incomplete; there is no automatic retry resumption or scheduler.
- Reference GTFS/live JSON mappings and environment readiness remain unverified.
  No arrival inference, trip reconstruction, or Phase 1 analytics were added.
