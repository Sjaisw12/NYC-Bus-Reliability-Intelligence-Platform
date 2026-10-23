"""Defaults to offline fixture ingestion; never starts a polling loop."""
import argparse
import json
import os
from pathlib import Path

from .core import LiveTransport, Response, Store, collect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/pilot"))
    parser.add_argument("--live", action="store_true", help="single live request; separate authorization required")
    args = parser.parse_args()
    try:
        store = Store(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    if args.live:
        if args.fixture or os.environ.get("MTA_COLLECTOR_ENABLE_LIVE") != "1":
            parser.error("live disabled: requires explicit --live and MTA_COLLECTOR_ENABLE_LIVE=1")
        try:
            transport = LiveTransport(enabled=True)
        except ValueError as exc:
            parser.error(str(exc))
        mode = "live"
    else:
        if args.fixture is None:
            parser.error("offline mode requires --fixture PATH")
        body = args.fixture.read_bytes()
        transport = lambda: Response(200, body)
        mode = "fixture"
    paths = collect(store, transport, mode=mode,
                    secret=os.environ.get("MTA_BUSTIME_API_KEY", ""))
    print(json.dumps(store.report(), sort_keys=True))
    for path in paths:
        meta = json.loads((path / "metadata.json").read_text())
        if meta["error"]:
            continue
        derived = store.ensure_derived(path)
        for line in (derived / "accepted.jsonl").read_text().splitlines():
            print(line)
    # A recovered retry is one successful logical poll, despite earlier failed attempts.
    if paths and json.loads((paths[-1] / "metadata.json").read_text())["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
