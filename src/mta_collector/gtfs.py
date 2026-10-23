"""Offline inspection of a downloaded official Brooklyn ZIP; no network access."""
import argparse
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

SOURCE = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_b.zip"


def inspect(path: Path, service_date: str) -> dict:
    day = date.fromisoformat(service_date)
    with ZipFile(path) as archive:
        def rows(name):
            return csv.DictReader(io.TextIOWrapper(archive.open(name), encoding="utf-8-sig"))
        required = {"agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt"}
        if not required <= set(archive.namelist()):
            raise ValueError("missing required GTFS tables")
        agencies = list(rows("agency.txt"))
        routes = [r for r in rows("routes.txt") if r["route_short_name"] == "B63"]
        if not routes:
            raise ValueError("B63 absent")
        route_ids = {r["route_id"] for r in routes}
        trips = [r for r in rows("trips.txt") if r["route_id"] in route_ids]
        if not trips:
            raise ValueError("B63 has no trips")
        if any(t.get("direction_id") not in ("0", "1") for t in trips):
            raise ValueError("invalid/missing B63 direction")
        if any(r.get("agency_id") not in {a["agency_id"] for a in agencies} for r in routes):
            raise ValueError("unresolved route agency")
        services = {r["service_id"] for r in trips}
        calendars = list(rows("calendar.txt")) if "calendar.txt" in archive.namelist() else []
        exceptions = list(rows("calendar_dates.txt")) if "calendar_dates.txt" in archive.namelist() else []
        active = set()
        weekday = day.strftime("%A").lower()
        compact = day.strftime("%Y%m%d")
        for r in calendars:
            if r["start_date"] <= compact <= r["end_date"] and r[weekday] == "1":
                active.add(r["service_id"])
        for r in exceptions:
            if r["date"] == compact:
                if r["exception_type"] == "1":
                    active.add(r["service_id"])
                else:
                    active.discard(r["service_id"])
        ids = {r["trip_id"] for r in trips}
        stops = {r["stop_id"] for r in rows("stops.txt")}
        sequences = {}
        for r in rows("stop_times.txt"):
            if r["trip_id"] in ids:
                if r["stop_id"] not in stops:
                    raise ValueError("unresolved stop")
                sequences.setdefault(r["trip_id"], []).append(int(r["stop_sequence"]))
        shapes = {r["shape_id"] for r in rows("shapes.txt")}
        if any(not r.get("shape_id") or r["shape_id"] not in shapes for r in trips):
            raise ValueError("unresolved shape")
        if any(not sequences.get(t) or len(set(sequences[t])) != len(sequences[t]) for t in ids):
            raise ValueError("missing/duplicate stop sequence")
        return {"source_url": SOURCE, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size, "service_date_checked": service_date,
                "agencies": agencies, "routes": routes,
                "candidate_line_refs": [r.get("agency_id", "") + "_" + r["route_id"] for r in routes],
                "directions": sorted({r["direction_id"] for r in trips}),
                "trip_count": len(trips), "active_trip_count": sum(r["service_id"] in active for r in trips),
                "service_calendars": [r for r in calendars if r["service_id"] in services],
                "service_exceptions": [r for r in exceptions if r["service_id"] in services],
                "stop_sequences_and_shapes_resolve": True,
                "live_line_ref_verified": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip", type=Path)
    parser.add_argument("--service-date", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.zip, args.service_date), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
