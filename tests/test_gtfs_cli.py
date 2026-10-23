import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from mta_collector.cli import main
from mta_collector.gtfs import inspect


class GtfsCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Path(__file__).parent / "fixtures/vehicle.json"
        blocked = patch.object(socket, "socket", side_effect=AssertionError("network forbidden"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def feed(self, **overrides):
        tables = {
            "agency.txt": "agency_id,agency_name\nMTA NYCT,Synthetic agency\n",
            "routes.txt": "route_id,agency_id,route_short_name\nB63,MTA NYCT,B63\n",
            "trips.txt": "route_id,service_id,trip_id,direction_id,shape_id\nB63,S,T0,0,G\nB63,S,T1,1,G\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nS,1,1,1,1,1,0,0,20261001,20261031\n",
            "calendar_dates.txt": "service_id,date,exception_type\nS,20261023,2\nS,20261101,1\n",
            "stops.txt": "stop_id\nSTOP\n",
            "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time\nT0,STOP,1,25:01:00\nT1,STOP,1,25:02:00\n",
            "shapes.txt": "shape_id\nG\n",
        }
        tables.update(overrides)
        path = self.root / "synthetic.zip"
        with ZipFile(path, "w") as z:
            for name, value in tables.items():
                z.writestr(name, value)
        return path

    def test_gtfs_identity_and_calendar_exceptions(self):
        path = self.feed()
        result = inspect(path, "2026-10-22")
        self.assertEqual(result["directions"], ["0", "1"])
        self.assertEqual(result["candidate_line_refs"], ["MTA NYCT_B63"])
        self.assertEqual(result["active_trip_count"], 2)
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(inspect(path, "2026-10-23")["active_trip_count"], 0)
        self.assertEqual(inspect(path, "2026-11-01")["active_trip_count"], 2)
        self.assertEqual(inspect(path, "2026-11-02")["active_trip_count"], 0)
        self.assertFalse(result["live_line_ref_verified"])

    def test_gtfs_wrong_agency_and_missing_shape_fail(self):
        with self.assertRaises(ValueError):
            inspect(self.feed(**{"agency.txt": "agency_id\nWRONG\n"}), "2026-10-22")
        with self.assertRaises(ValueError):
            inspect(self.feed(**{"shapes.txt": "shape_id\nWRONG\n"}), "2026-10-22")

    def test_cli_defaults_offline_and_live_double_guard(self):
        for argv in (["collector"], ["collector", "--live"]):
            with patch("sys.argv", argv), patch.dict(os.environ, {}, clear=True):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main()
                self.assertEqual(exc.exception.code, 2)
        with patch("sys.argv", ["collector", "--fixture", str(self.fixture), "--output", str(self.root / "polls")]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                main()
        lines = output.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])["observations"], 1)
        self.assertEqual(json.loads(lines[1])["line_ref"], "MTA NYCT_B63")
        self.assertEqual(len(list((self.root / "polls").glob("poll-*"))), 1)
