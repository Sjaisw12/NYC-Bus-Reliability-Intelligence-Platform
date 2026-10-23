# Phase 0 data contract

Each `data/.../poll-<UUID>/` bundle contains `response.bin` (exact response bytes)
and `metadata.json`. This pair is the durable unit of commit. Raw format is kept
uncompressed for this tiny pilot; compression and Parquet are deferred.

Metadata includes poll ID, fixture/live mode, fixed route filter, sanitized endpoint
(no query string), HTTP status, attempt number, parser version, byte count, SHA-256,
error category, request start, response received, ingest, and provider response time.
UTC collector timestamps are separate from the vehicle's source recorded time.

Expected JSON paths (provisional until an authorized JSON v2 sample):
`Siri.ServiceDelivery.VehicleMonitoringDelivery[].VehicleActivity[]`,
`RecordedAtTime`, and `MonitoredVehicleJourney`. Normalization extracts LineRef,
VehicleRef, DirectionRef, framed journey date/trip, shape, location, MonitoredCall
next stop/distances, and progress indicators. Missing optional data stays null;
zero coordinates/distances are not interpreted as missing. Invalid coordinates,
missing identity/recorded time, and unexpected route are quarantined by ordinal.
Provider errors and malformed envelopes are poll failures. A valid empty list is
successful. Unknown source fields remain available in raw bytes.

Accepted and quarantined records are persisted separately under
`derived/poll-<attempt_id>/accepted.jsonl` and `quarantine.jsonl` (empty files for
empty groups). Each contains stable observation ID (`attempt_id:ordinal`), poll ID,
attempt ID, raw path/hash, parser and derived versions, mode, route filter, request,
response, ingest, provider, and available vehicle-recorded timestamps. Invalid
source timestamps stay null in quarantine; the original remains at its raw ordinal.
The two derived files are committed together via fsync and atomic directory rename.
Recovery recreates a missing derived bundle from committed raw; repeated recovery
verifies bytes rather than appending rows. Mismatched derived data raises an error.
No raw response is edited to perform recovery. Unfinished `.tmp-*` views are ignored.

Derived views are rebuilt from bundles, never written into the source payload.
CLI normalized rows link poll ID, raw path, and observation ordinal to request and
ingest timestamps. Quarantine is available via `parse()`; raw remains preserved.
Duplicate identity is vehicle + recorded timestamp + route + direction + trip.
Duplicates stay in raw and counts; report marks their count without silent removal.
Source age >120 seconds is provisionally stale; negative age is separately flagged.
This threshold is a QA convention, not a guarantee of feed accuracy or quota.

GTFS hashes/effective dates are not attached to observations yet because the official
feed could not be downloaded. No GTFS join, service-date inference, journey
reconstruction, arrival estimation, or headway calculation is implemented. The
source service date is preserved as supplied; it is not inferred from UTC date.

Credential-echo exception: a payload containing the configured key (literal or URL
encoded) or a recognizable `key=` query parameter is rejected before storage;
an empty body plus `unsafe_credential_echo` records that intentional rejection.
A conservative credential-field/UUID-pattern check also rejects possible raw-key
echoes without knowing the proxy's real value. It can reject legitimate UUID-bearing
payloads and cannot recognize every possible encoded credential. HTTP error bodies
and arbitrary transport exception text are deliberately omitted.
Such failures do not count as retained provider observations.

## Poll and attempt accounting (correction)

A collect call creates one logical poll ID shared by all retries. Every issued
transport attempt has a distinct attempt ID and immutable raw/metadata bundle.
`terminal_attempt` records whether retry is complete, including long Retry-After
stops. Health separates http_attempts (including transport failures), failed_attempts,
and successful_attempts from persisted_polls, successful_polls, failed_polls, and
incomplete_polls. A failed attempt followed by success is one successful poll.
A crash after a retryable failed attempt and before the next attempt leaves an
incomplete poll. Unpersisted attempts cannot be counted. Pre-correction bundles,
which lack logical-poll linkage, remain independent terminal polls; no historical
retry grouping is invented. Repeated source pings across polls remain preserved
and counted as duplicates, separately from idempotent recovery.
