# Phase 0 operations and source access

Single writer, local POSIX filesystem only. No hosted service, scheduler, deployment,
cloud storage, or expenses are provisioned. Do not use the temporary workspace as
proof of continuous durable hosting.

Writes flush/fsync each file, fsync the temporary directory, rename the directory,
and fsync its parent. Completed bundles are application-immutable and never edited
by the collector. This is not filesystem-enforced WORM storage. Acknowledgement is
a successful return from `save()`. Before-commit crashes leave ignored `.tmp-*`
directories; after-commit crashes leave recoverable complete raw bundles. Separate
accepted/quarantine views are materialized atomically and recovered idempotently. There is no
mutable checkpoint to lose. `Store.report()` reconstructs state and verifies hashes.
Interrupted fetches before durable save are unknown attempts, not recovered data.
Storage failure aborts fetching immediately. Do not delete temporary artifacts until
review; they are never silently promoted to successful polls. Run recovery/report
with no writer active. Concurrent collectors are outside Phase 0 scope.

Single-poll transport uses a 15-second timeout and fixed HTTPS destination, LineRef,
version=2 and normal detail. Redirects are refused. Inherited proxy and TLS trust
remain in force. Only MTA_BUSTIME_API_KEY supplies credentials; .env files are not
automatically read. Never paste a key into shell arguments, logs, tests, or config.
No authenticated request was used to verify this implementation.

Timeouts and HTTP 429/503: at most three persisted attempts, exponential waits of
2 and 4 seconds unless Retry-After specifies otherwise. Numeric and HTTP-date
Retry-After are supported; waits over 60 seconds stop the poll without an early
retry. Other failures, including 401/403, stop immediately. A single-poll command
exits after completion and returns failure only if its final attempt failed. A
successful retry returns success; earlier failed attempts remain in health counts. Stop a process
with Ctrl-C; do not start a second writer during shutdown.

The stdout health summary separates logical polls from individual attempts (see
data-contract.md) and reports successes/failures/incomplete polls, rows,
quarantine, duplicates, stale counts, source-age samples/P50/P95, bytes, and latest
ingest. It is a post-run health snapshot, not an independent liveness heartbeat,
daily scheduler, uptime monitor, or route-minute coverage report. Those require
an approved host/window contract. Restart tests do not establish power-loss safety
on every filesystem or persistence across workspace deletion.

## Official static feed verification (pending network readiness)

Official index: https://www.mta.info/developers
Brooklyn ZIP: https://rrgtfsfeeds.s3.amazonaws.com/gtfs_b.zip
Transition guidance: https://www.mta.info/developers/gtfs-schedules-transition

After the proxy and domain configuration work, download the ZIP unauthenticated
into ignored reference storage, record UTC retrieval time and HTTP metadata, retain
the original ZIP, then run the offline inspector with the intended service date:

```sh
PYTHONPATH=src python -m mta_collector.gtfs data/reference/gtfs_b.zip --service-date 2026-10-22
```

The inspector checks B63 route/agency linkage, directions, service calendars and
exceptions, stop references/sequences, shape resolution, and SHA-256. Its candidate
LineRef still needs live verification. It never extends service calendars. The
synthetic GTFS tests do not verify the current official schedule.

API reference: https://bustime-classic.mta.info/wiki/Developers/SIRIVehicleMonitoring
Journey semantics: https://bustime-classic.mta.info/wiki/Developers/SIRIMonitoredVehicleJourney
Terms: https://www.mta.info/developers/terms-and-conditions

Preserve originals separately from derived estimates. No public redistribution,
branding, accuracy/timeliness claim, or application release is authorized here.

## Output and credential safety corrections

Store roots must resolve strictly below this repository's ignored `data/` tree or
`/tmp/` for disposable offline tests. Other roots and existing symlinks in the path
are rejected before fetching; symlinked raw/derived paths are rejected during
recovery. Use data/pilot for persistent review evidence. /tmp is disposable, not a
production storage guarantee. Do not point a collector at a different Git checkout
under /tmp. The single-writer, trusted-local-filesystem assumption remains; this is
not protection against a concurrent malicious filesystem writer.

Additional ignores cover credentials directories/files, private key formats,
logs/archives, reports/output/artifacts, poll directories, derived filenames, and
.tmp-* anywhere. These are defense in depth, not a universal secret scanner.
Sanitized documentation, .env.example, and synthetic JSON fixtures remain visible.
Never force-add ignored output or put credentials in arbitrary source filenames.

The network secret variable is opaque and may contain only a placeholder. The entire
fixed parameter mapping, including the credential as one `key` value, is encoded
with standard-library `urlencode(..., quote_via=quote, safe="")`. Reserved
characters, percent signs, spaces, and Unicode round-trip as value data; they cannot
add parameters or change the fixed version, B63 LineRef, or detail level. Empty
values, C0/C1 controls and DEL, and unpaired Unicode surrogates fail before
transport with value-free errors. Surrogates are rejected because they cannot be
encoded as valid UTF-8. No decoding, normalization, or local substitution occurs.
Inherited HTTPS proxy/TLS trust, redirect refusal, live authorization gates,
15-second timeout, and bounded retries remain unchanged. Raw and percent-encoded
local credential echoes are rejected before persistence; credential-field/UUID
checks remain conservative safeguards for unknown substituted values.

On 2026-10-23, one separately authorized disposable synthetic credential experiment
returned HTTP 200 and verified percent-encoded placeholder substitution and query
parameter isolation through this environment's proxy. No actual MTA request was
made. This proves behavior only for that disposable credential in that environment;
it does not establish universal proxy behavior or handling of arbitrary real
credential values. The implementation tests use synthetic credentials and mocked
transport only. No real MTA credential or its placeholder was accessed for this
correction. Provider echo detection cannot recognize every possible encoding or
secret format; raw data remains private and ignored. Public/raw release still
requires independent review. An authenticated MTA smoke test remains a separate
authorization and review gate.
