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
- core.py: credential query encoding was subsequently corrected; see the compatibility
  correction below. The original URL-safe-only restriction is superseded.
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

- The disposable synthetic experiment confirmed encoded substitution for one
  credential in one environment, not universal proxy behavior or arbitrary real
  credential values. No authenticated MTA compatibility check has been performed;
  that smoke test remains a separate authorization and review gate.
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
- Official static Brooklyn GTFS was separately verified; see
  [gtfs-brooklyn-verification.md](gtfs-brooklyn-verification.md). Live GTFS/SIRI
  mappings and authenticated MTA collection readiness remain unverified.
  No arrival inference, trip reconstruction, or Phase 1 analytics were added.

## Credential compatibility correction (2026-10-23)

The complete fixed query mapping now uses `urlencode(..., quote_via=quote, safe="")`,
including the opaque credential as a single `key` value. Reserved characters,
percent signs, spaces, and Unicode remain value data. Empty credentials, C0/C1
controls including DEL, and unpaired surrogates are rejected with value-free
errors; surrogates cannot be represented as valid UTF-8. Existing raw and
percent-encoded credential-echo checks remain unchanged.

The exact HTTPS endpoint, proxy/TLS handling, redirect refusal, live-mode gates,
15-second timeout, retry bounds, and fixed B63/version/detail values are unchanged.
Targeted offline tests cover round-trip encoding, parameter injection attempts,
invalid credentials, raw/encoded echoes, mocked requests, redirect refusal, live
mode authorization, and sanitized errors without URL output or logging.

The separately approved disposable experiment returned HTTP 200 with successful
encoded substitution and parameter isolation. It proves only that one synthetic
credential's behavior in that environment, not universal proxy behavior or handling
of arbitrary real credential values. This correction uses no actual MTA credential
or placeholder and makes no network requests. Earlier test results above describe
the original correction; updated verification is recorded below.

Verification command (credential variables removed from the test subprocess):

```sh
env -u MTA_BUSTIME_API_KEY -u CODEX_QUERY_CANARY PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Exit 0: Ran 39 tests in 0.106s; OK (all 33 existing cases, with the obsolete
format-rejection case adapted, plus six additional test methods). All test classes
block sockets; transport requests and redirect responses are mocked. No live
collector invocation or HTTP experiment was performed. `git diff --check` passed.
Diff review found no endpoint, gate, timeout, retry, dependency, storage, or
credential-echo safeguard changes; production edits are limited to query construction.
