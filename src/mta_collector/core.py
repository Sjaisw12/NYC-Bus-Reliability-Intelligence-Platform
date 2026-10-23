"""Auditable single-route retrieval, parsing, and durable local storage.

Only LiveTransport can access the network. All other operations are offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener, Request
from uuid import uuid4

ENDPOINT = "https://bustime.mta.info/api/siri/vehicle-monitoring.json"
REPOSITORY = Path(__file__).resolve().parents[2]
DATA_ROOT = REPOSITORY / "data"


def storage_path(root: Path) -> Path:
    """Restrict persistent artifacts to ignored data/ or disposable /tmp storage."""
    candidate = Path(root).absolute()
    if any(p.is_symlink() for p in (candidate, *candidate.parents)):
        raise ValueError("storage path must not contain symlinks")
    resolved = candidate.resolve()
    if not any(resolved.is_relative_to(base) and resolved != base
               for base in (DATA_ROOT, Path("/tmp"))):
        raise ValueError("storage must be below repository data/ or /tmp/")
    return resolved


def credential_query(credential: str) -> str:
    # Preserve the opaque placeholder byte-for-byte: never percent-encode it.
    # Unknown placeholder formats fail closed rather than guessing proxy behavior.
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", credential):
        raise ValueError("credential placeholder is not URL-safe; proxy compatibility unverified")
    return urlencode({"version": 2, "LineRef": ROUTE,
                      "VehicleMonitoringDetailLevel": "normal"}) + "&key=" + credential


ROUTE = "MTA NYCT_B63"  # Candidate, not a verified current identifier.


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp requires offset")
    return dt.astimezone(timezone.utc)


def redact(value: str, secret: str = "") -> str:
    if secret:
        for token in (secret, quote(secret, safe="")):
            value = value.replace(token, "[REDACTED]")
    return re.sub(r"(?i)(key=)[^&\s\"<>]+", r"\1[REDACTED]", value)


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    retry_after: str | None = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a query credential to another destination.


class LiveTransport:
    def __init__(self, *, enabled: bool = False):
        if not enabled:
            raise ValueError("live mode disabled; explicit authorization required")
        self._secret = os.environ.get("MTA_BUSTIME_API_KEY", "")
        if not self._secret:
            raise ValueError("MTA_BUSTIME_API_KEY is required")
        self._query = credential_query(self._secret)
        self._opener = build_opener(ProxyHandler(), NoRedirect())

    def __call__(self) -> Response:
        # Route is fixed to the sole Phase 0 candidate; no arbitrary URLs/filters.
        params = self._query
        try:
            with self._opener.open(Request(ENDPOINT + "?" + params), timeout=15) as r:
                return Response(r.status, r.read(), r.headers.get("Retry-After"))
        except HTTPError as exc:
            # Do not expose exception URLs, server error bodies, or headers.
            return Response(exc.code, b"", exc.headers.get("Retry-After"))
        except Exception:
            raise OSError("transport failure") from None


def parse(body: bytes, ingested_at: str, route: str = ROUTE) -> dict:
    """Expected JSON v2 paths are provisional until live schema verification."""
    result = {"observations": [], "quarantine": [], "error": None}
    try:
        payload = json.loads(body)
        delivery = payload["Siri"]["ServiceDelivery"]
        provider_time = delivery.get("ResponseTimestamp")
        result["provider_response_at"] = instant(provider_time).isoformat() if provider_time is not None else None
        batches = delivery["VehicleMonitoringDelivery"]
        if not isinstance(batches, list) or not batches:
            raise ValueError()
        for batch in batches:
            if batch.get("ErrorCondition") or batch.get("Status") is False:
                result["error"] = "provider_error"
                return result
            activities = batch.get("VehicleActivity", [])
            if not isinstance(activities, list):
                raise ValueError()
            for activity in activities:
                ordinal = len(result["observations"]) + len(result["quarantine"])
                try:
                    journey = activity["MonitoredVehicleJourney"]
                    recorded = instant(activity["RecordedAtTime"])
                    if journey["LineRef"] != route:
                        raise ValueError()
                    for field in ("DirectionRef", "JourneyPatternRef", "ProgressRate", "ProgressStatus"):
                        if journey.get(field) is not None and not isinstance(journey[field], str):
                            raise ValueError()
                    vehicle = journey["VehicleRef"]
                    if not isinstance(vehicle, str) or not vehicle:
                        raise ValueError()
                    location = journey["VehicleLocation"]
                    lat, lon = location["Latitude"], location["Longitude"]
                    if (type(lat) not in (int, float) or type(lon) not in (int, float)
                            or not math.isfinite(lat) or not math.isfinite(lon)
                            or not -90 <= lat <= 90 or not -180 <= lon <= 180):
                        raise ValueError()
                    age = (instant(ingested_at) - recorded).total_seconds()
                    frame = journey.get("FramedVehicleJourneyRef") or {}
                    call = journey.get("MonitoredCall") or {}
                    for value in (frame.get("DataFrameRef"), frame.get("DatedVehicleJourneyRef"), call.get("StopPointRef")):
                        if value is not None and not isinstance(value, str):
                            raise ValueError()
                    row = {"ordinal": ordinal, "vehicle_ref": vehicle, "line_ref": route,
                           "direction_ref": journey.get("DirectionRef"),
                           "recorded_at_utc": recorded.isoformat(),
                           "latitude": lat, "longitude": lon,
                           "service_date_source": frame.get("DataFrameRef"),
                           "trip_ref": frame.get("DatedVehicleJourneyRef"),
                           "shape_ref": journey.get("JourneyPatternRef"),
                           "next_stop_ref": call.get("StopPointRef"),
                           "distances": (call.get("Extensions") or {}).get("Distances"),
                           "progress_rate": journey.get("ProgressRate"),
                           "progress_status": journey.get("ProgressStatus"),
                           "source_age_seconds": age,
                           "flags": (["stale"] if age > 120 else []) +
                                    (["future_source_time"] if age < 0 else [])}
                    result["observations"].append(row)
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    try:
                        source_time = instant(activity.get("RecordedAtTime")).isoformat()
                    except (AttributeError, TypeError, ValueError, OverflowError):
                        source_time = None
                    result["quarantine"].append({"ordinal": ordinal, "reason": "invalid_observation",
                                                 "recorded_at_utc": source_time})
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
        result["error"] = "malformed_envelope"
        result["observations"] = []
        result["quarantine"] = []
    return result


class Store:
    """Single-writer store; immutable bundles committed with atomic directory rename.

    Completed directories are the checkpoint. No mutable index is required for recovery.
    An interrupted .tmp directory is ignored; a completed bundle is never overwritten.
    """
    def __init__(self, root: Path, fault: Callable[[str], None] = lambda stage: None):
        self.root = storage_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.fault = fault

    def bundles(self):
        paths = sorted(p for p in self.root.glob("poll-*") if p.is_dir())
        if any(p.is_symlink() for p in paths):
            raise ValueError("symlink in raw storage")
        return paths

    def save(self, meta: dict, body: bytes) -> Path:
        identity = meta.get("attempt_id", meta["poll_id"])
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("invalid storage identity")
        name = "poll-" + identity
        target = self.root / name
        if target.exists():
            raise FileExistsError("poll already committed")
        temp = self.root / (".tmp-" + uuid4().hex)
        temp.mkdir()
        for filename, content in (("response.bin", body),
                                  ("metadata.json", json.dumps(meta, sort_keys=True).encode())):
            with (temp / filename).open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self.fault(filename)
        self._sync(temp)
        os.rename(temp, target)
        self._sync(self.root)
        self.fault("committed")
        self.ensure_derived(target)
        return target

    @staticmethod
    def _sync(path: Path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_bundle(self, bundle: Path) -> tuple[dict, bytes]:
        if bundle.is_symlink() or any((bundle / name).is_symlink()
                                      for name in ("metadata.json", "response.bin")):
            raise ValueError("symlink in raw storage")
        meta = json.loads((bundle / "metadata.json").read_text())
        body = (bundle / "response.bin").read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["payload_sha256"]:
            raise ValueError("payload checksum mismatch")
        return meta, body

    def ensure_derived(self, bundle: Path) -> Path:
        """Idempotent per-attempt materialization; raw bundles are never edited."""
        meta, body = self.read_bundle(bundle)
        derived_root = storage_path(self.root / "derived")
        derived_root.mkdir(exist_ok=True)
        target = derived_root / bundle.name
        if target.is_symlink():
            raise ValueError("symlink in derived storage")
        parsed = parse(body, meta["ingested_at_utc"]) if not meta["error"] else {}
        lineage = {key: meta.get(key) for key in (
            "poll_id", "attempt_id", "mode", "route_filter", "parser_version",
            "request_started_at_utc", "response_received_at_utc", "ingested_at_utc",
            "provider_response_at", "payload_sha256")}
        lineage["derived_version"] = "0.2.0"
        lineage["attempt_id"] = meta.get("attempt_id", meta["poll_id"])
        lineage["raw_path"] = str(bundle / "response.bin")
        expected = {}
        for filename, key in (("accepted.jsonl", "observations"), ("quarantine.jsonl", "quarantine")):
            records = [{**row, **lineage,
                        "observation_id": lineage["attempt_id"] + ":" + str(row["ordinal"])}
                       for row in parsed.get(key, [])]
            expected[filename] = "".join(json.dumps(row, sort_keys=True) + "\n" for row in records).encode()
        if target.exists():
            # Verify committed views; never append or silently overwrite corruption.
            for filename, content in expected.items():
                file = target / filename
                if file.is_symlink() or file.read_bytes() != content:
                    raise ValueError("derived data mismatch")
            return target
        temp = derived_root / (".tmp-" + uuid4().hex)
        temp.mkdir()
        for filename, content in expected.items():
            with (temp / filename).open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self.fault(filename)
        self._sync(temp)
        os.rename(temp, target)
        self._sync(derived_root)
        self.fault("derived_committed")
        return target

    def recover(self) -> None:
        for bundle in self.bundles():
            self.ensure_derived(bundle)

    def report(self) -> dict:
        self.recover()
        attempts = failures = rows = quarantine = duplicates = stale = total_bytes = 0
        seen, ages, polls = set(), [], {}
        last_ingest = None
        for bundle in self.bundles():
            meta, body = self.read_bundle(bundle)
            attempts += 1
            failures += bool(meta["error"])
            logical_id = meta.get("logical_poll_id", meta["poll_id"])
            state = polls.setdefault(logical_id, {"success": False, "terminal": False})
            state["success"] |= not bool(meta["error"])
            # Old bundles have no logical poll contract: count as independent terminal polls.
            state["terminal"] |= meta.get("terminal_attempt", True)
            total_bytes += meta["bytes"]
            last_ingest = max(last_ingest or meta["ingested_at_utc"], meta["ingested_at_utc"])
            derived = self.root / "derived" / bundle.name
            quarantine += len((derived / "quarantine.jsonl").read_text().splitlines())
            for line in (derived / "accepted.jsonl").read_text().splitlines():
                row = json.loads(line)
                identity = (row["vehicle_ref"], row["recorded_at_utc"],
                            row["line_ref"], row["direction_ref"], row["trip_ref"])
                duplicates += identity in seen
                seen.add(identity)
                rows += 1
                stale += "stale" in row["flags"]
                ages.append(row["source_age_seconds"])
        succeeded = sum(p["success"] for p in polls.values())
        failed = sum(p["terminal"] and not p["success"] for p in polls.values())
        return {"persisted_polls": len(polls), "payload_bytes": total_bytes,
                "last_ingested_at_utc": last_ingest, "failed_polls": failed,
                "successful_polls": succeeded, "incomplete_polls": len(polls) - succeeded - failed,
                "http_attempts": attempts, "failed_attempts": failures,
                "successful_attempts": attempts - failures,
                "observations": rows, "quarantined_rows": quarantine,
                "duplicate_observations": duplicates, "stale_observations": stale,
                "source_ages_seconds": ages,
                "source_age_p50_seconds": sorted(ages)[(len(ages)-1)//2] if ages else None,
                "source_age_p95_seconds": sorted(ages)[math.ceil(.95*len(ages))-1] if ages else None,
                "scope": "persisted logical polls and attempts; no scheduled coverage denominator"}


def retry_delay(header: str | None, now: str, fallback: float) -> float:
    if header:
        try:
            return max(0, float(header))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(header) - instant(now)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return fallback


def collect(store: Store, transport: Callable[[], Response], *, mode="fixture",
            clock=utcnow, sleep=time.sleep, secret="", max_attempts=3) -> list[Path]:
    if mode not in ("fixture", "live") or not 1 <= max_attempts <= 3:
        raise ValueError("invalid mode/attempt bound")
    paths = []
    logical_poll_id = uuid4().hex
    for attempt in range(max_attempts):
        start = clock()
        try:
            response = transport()
            received = clock()
            error = None if response.status == 200 else "http_failure"
        except Exception:
            response = Response(0, b"")
            received = clock()
            error = "transport_failure"
        ingested = clock()
        parsed = parse(response.body, ingested) if not error else {}
        error = error or parsed.get("error")
        # An unexpected credential echo is rejected, never written as raw data.
        body = response.body
        redacted = redact(body.decode("utf-8", errors="replace"), secret)
        # The proxy may substitute an unknown raw key. Reject credential-shaped
        # echoes as well as the local placeholder; do not depend on knowing the key.
        suspicious_echo = bool(re.search(
            rb'(?i)"(?:key|api[_-]?key|authorization|access[_-]?token)"\s*:|'
            rb'\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b', body))
        if suspicious_echo or (secret and (secret.encode() in body or quote(secret, safe="").encode() in body)) or redacted != body.decode("utf-8", errors="replace"):
            body = b""
            error = "unsafe_credential_echo"
            parsed = {}
        retry = bool(error) and response.status in (0, 429, 503) and attempt + 1 < max_attempts
        delay = retry_delay(response.retry_after, ingested, 2 ** (attempt + 1)) if retry else 0
        retry = retry and math.isfinite(delay) and delay <= 60
        meta = {"poll_id": logical_poll_id, "logical_poll_id": logical_poll_id,
                "attempt_id": uuid4().hex, "terminal_attempt": not retry, "mode": mode, "route_filter": ROUTE,
                "sanitized_endpoint": ENDPOINT, "attempt": attempt + 1,
                "request_started_at_utc": start, "response_received_at_utc": received,
                "ingested_at_utc": ingested, "http_status": response.status,
                "provider_response_at": parsed.get("provider_response_at"),
                "payload_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                "parser_version": "0.2.0", "error": error}
        paths.append(store.save(meta, body))  # Storage exceptions stop; never retry fetch.
        if not retry:
            break
        sleep(delay)
    return paths
