# Phase 0: official Brooklyn GTFS verification

The downloaded official Brooklyn feed contains B63 and schedules **183 trips for
Friday, October 23, 2026**, interpreted in `America/New_York`. These findings come
from the downloaded official ZIP, not synthetic fixtures. The independent reviewer
approved the verification evidence before this documentation was prepared.

Baseline: branch `work`, tracking `origin/work`, commit
`022e8b9bdc749cffdb257e82f650c7249e5a58ea`. The working tree was clean before
this documentation change.

## Source and retrieval provenance

The [MTA developer resources](https://www.mta.info/developers) link the Brooklyn
static GTFS download to
<https://rrgtfsfeeds.s3.amazonaws.com/gtfs_b.zip>. The final response URL was the
same as the requested URL. Retrieval was unauthenticated.

| Field | Observed value |
| --- | --- |
| Retrieval started, UTC | `2026-10-23T07:37:48.398335+00:00` |
| Retrieval completed, UTC | `2026-10-23T07:37:49.099644+00:00` |
| HTTP status | `200` |
| HTTP Date | `Fri, 23 Oct 2026 07:37:49 GMT` |
| Last-Modified | `Fri, 16 Oct 2026 15:14:43 GMT` |
| ETag | `"00766c01563325dc282e6414ef49127b"` |
| Content-Type | `application/zip` |
| Content-Length and downloaded bytes | `17046285` |
| Accept-Ranges | `bytes` |
| Server | `envoy` |
| S3 server-side encryption | `AES256` |
| Feed publisher | MTA New York City Transit |
| Feed version | `20261009` |
| Feed language | `en` |

Exact ZIP SHA-256:

```text
d3d1113cbb4038d89982e97956a3891c748f8611ec7fdc8e22492ed7a9e4ed32
```

The ETag is recorded as HTTP metadata; SHA-256 identifies the inspected bytes.
The browser could not render the ZIP content, but the network-enabled download
succeeded and ZIP CRC validation found no corrupt member.

## Coverage and service-date interpretation

`feed_info.txt` declares `feed_start_date=20261012` and
`feed_end_date=20270102`, or October 12, 2026 through January 2, 2027. Across
`calendar.txt`, individual ranges span October 8, 2026 through January 2, 2027.
Some calendar rows therefore start before the declared feed start. This boundary
difference does not affect October 23, which is inside both the declared feed
coverage and the applicable B63 calendar.

The agency timezone is `America/New_York`. The requested date is a New York GTFS
service date, not the UTC retrieval date or an assertion that every scheduled
departure falls within that civil day's midnight-to-midnight interval. GTFS times
beyond 24:00 remain associated with their original service date.

Calendar evaluation first selects services whose inclusive date range contains
the target date and whose Friday flag is enabled. It then applies
`calendar_dates.txt`: type `1` adds service; type `2` removes service. No calendar
dates were altered or extended.

## B63 identity and scheduled service

| Field | Exact value |
| --- | --- |
| route_id / route_short_name | `B63` |
| agency_id | `MTA NYCT` |
| Agency name | MTA New York City Transit |
| route_long_name | `Bay Ridge - Cobble Hill` |
| route_desc | `via 5th Av / Atlantic Av` |
| route_type | `3` (bus) |
| direction_id values | `0`, `1` |

The feed contains 860 B63 trip records across five service IDs. Counts in the
middle columns describe all records for each service, not simultaneous daily
service.

| service_id | Direction 0 | Direction 1 | Base calendar | Status on October 23 |
| --- | ---: | ---: | --- | --- |
| `JG_D6-Weekday-SDon` | 92 | 91 | Weekdays, 2026-10-09–2026-12-23 | Active: 183 trips |
| `JG_D6-Saturday` | 92 | 98 | Saturdays, 2026-10-10–2027-01-02 | Inactive weekday |
| `JG_D6-Sunday` | 74 | 78 | Sundays, 2026-10-11–2027-01-01 | Inactive weekday |
| `JG_O6-Weekday` | 79 | 81 | Weekdays, 2026-10-12–2026-12-31 | Removed by type-2 exception |
| `JG_D6-Weekday` | 86 | 89 | Weekdays, 2026-11-03–2026-11-11 | Outside date range |

The only B63 exception on `20261023` removes `JG_O6-Weekday`. Counting its
160 trips from the base calendar alone would overcount service. After exceptions,
only `JG_D6-Weekday-SDon` is active: **92 direction-0 trips plus 91 direction-1
trips = 183 scheduled trips**.

Direction-0 headsigns are `PIER 6 BKLYN BRIDGE PARK via 5 AV` and `39 ST - 5 AV`.
Direction-1 headsigns are `BAY RIDGE SHORE RD via 5 AV` and `33 ST - 5 AV`.
Direction numbers are feed identifiers, not independently verified SIRI direction
codes. These are scheduled trip counts, not observed vehicle runs.

## Tables and reference integrity

All nine requested tables are present. Counts exclude CSV headers.

| Table | Data rows |
| --- | ---: |
| agency.txt | 1 |
| routes.txt | 307 |
| trips.txt | 58,357 |
| stops.txt | 4,575 |
| stop_times.txt | 2,399,470 |
| shapes.txt | 82,799 |
| calendar.txt | 36 |
| calendar_dates.txt | 455 |
| feed_info.txt | 1 |

The existing inspector passed B63 agency linkage, direction, stop-reference,
stop-sequence, and shape-reference checks. Supplemental offline inspection of the
same ZIP established the following across all 860 B63 trips, including inactive
services:

- All 860 trip IDs are unique; all trips have stop-time rows.
- 46,669 stop-time rows reference 110 distinct existing stops.
- Each trip has 12–55 stops, with strictly increasing, unique stop sequences.
- All seven referenced shapes exist: `B630016`, `B630031`, `B630041`,
  `B630081`, `B630082`, `B630083`, and `B630084`.
- Those shapes contain respectively 122, 215, 58, 320, 298, 103, and 320 points.
  Point sequences are strictly increasing and unique; coordinates are within
  latitude/longitude bounds. The active trips collectively reference all seven.

These checks establish B63 reference integrity, not full-feed GTFS conformance,
geometric alignment of stops to shapes, or on-street operational accuracy.

## Reproducing the inspection

Run from the repository root with Python 3.12+; no additional packages are needed.
For the retained original ZIP, verify its hash and inspect the intended date:

```sh
sha256sum data/reference/gtfs_b.zip
wc -c < data/reference/gtfs_b.zip
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m mta_collector.gtfs \
  data/reference/gtfs_b.zip --service-date 2026-10-23
git check-ignore -v data/reference/gtfs_b.zip
```

The inspector emits the source URL, hash, size, agencies, B63 route, directions,
total and active trip counts, relevant calendars/exceptions, reference-check
result, and `live_line_ref_verified: false`. It does not emit the supplemental
table counts or strict sequence-order/coordinate checks described above.

Reproduce the table counts and feed metadata directly from the same ZIP:

```sh
python - <<'PY'
import csv
import io
from zipfile import ZipFile

with ZipFile("data/reference/gtfs_b.zip") as archive:
    print("CRC error member:", archive.testzip())
    for name in archive.namelist():
        if name.endswith(".txt"):
            with archive.open(name) as stream:
                rows = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"))
                print(name, sum(1 for _ in rows))
    with archive.open("feed_info.txt") as stream:
        print(list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"))))
PY
```

If the original is unavailable, a separately authorized network-enabled execution
can retrieve a new snapshot without credentials. Use a fresh ignored filename,
record UTC timestamps and headers, and compare its hash before treating it as the
same evidence:

```sh
mkdir -p data/reference
date -u +%Y-%m-%dT%H:%M:%SZ
curl --fail --silent --show-error --location --max-time 60 \
  --dump-header data/reference/gtfs_b-new.headers \
  --output data/reference/gtfs_b-new.zip \
  --write-out 'HTTP %{http_code}; URL %{url_effective}; bytes %{size_download}\n' \
  https://rrgtfsfeeds.s3.amazonaws.com/gtfs_b.zip
date -u +%Y-%m-%dT%H:%M:%SZ
sha256sum data/reference/gtfs_b-new.zip
```

Choose another filename if either output already exists. Do not overwrite the
retained original or infer that a replacement ZIP reproduces this report.

## Static consistency, live verification, and retention limits

Combining static `agency_id` and `route_id` yields the candidate
`MTA NYCT_B63`, consistent with the project's proposed SIRI LineRef. Static GTFS
does not prove that a live endpoint accepts this LineRef, returns vehicles, uses
matching direction/trip/stop identifiers, or provides usable journey semantics.
Those questions remain unverified and require separately authorized live checks.
No authenticated MTA requests were made, no API key was accessed, and live
collection or continuous polling was not enabled.

The [official developer resources](https://www.mta.info/developers) describe bus
feeds as quarterly schedules that generally omit temporary changes and detours.
No target-date service gap was found in this snapshot. The
[MTA transition guidance](https://www.mta.info/developers/gtfs-schedules-transition)
explains that new schedules can replace old downloads before becoming effective;
consumers should hold or merge schedules rather than extend calendar dates.

The original ZIP and generated retrieval/inspection evidence are stored only in
ignored `data/reference/` storage. They are not included in Git and may disappear
when this temporary workspace is deleted or replaced. The public URL can serve a
different quarterly snapshot later; this report's hash and metadata identify only
the inspected download. Reproducing the exact evidence requires retaining those
original bytes outside disposable storage through a separately approved retention
process. This document contains no raw bus responses, credentials, local absolute
paths, or generated runtime files.
