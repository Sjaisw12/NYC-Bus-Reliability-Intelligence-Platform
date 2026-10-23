import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from mta_collector.core import (LiveTransport, Response, Store, collect, parse,
                                redact, retry_delay)

NOW = "2026-10-22T12:00:00+00:00"
BODY = (Path(__file__).parent / "fixtures/vehicle.json").read_bytes()


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        # Every test must be offline, including configuration tests.
        self.block = patch.object(socket, "socket", side_effect=AssertionError("network forbidden"))
        self.block.start()
        self.addCleanup(self.block.stop)

    def run_poll(self, response=Response(200, BODY), **kwargs):
        return collect(self.store, lambda: response, clock=lambda: NOW, **kwargs)

    def test_live_disabled_and_missing_key(self):
        with self.assertRaises(ValueError):
            LiveTransport()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            LiveTransport(enabled=True)

    def test_valid_timestamps_and_lineage(self):
        path = self.run_poll()[0]
        meta = json.loads((path / "metadata.json").read_text())
        self.assertEqual(meta["mode"], "fixture")
        self.assertEqual(meta["ingested_at_utc"], NOW)
        row = parse(BODY, NOW)["observations"][0]
        self.assertEqual(row["source_age_seconds"], 30)
        self.assertEqual(row["ordinal"], 0)
        self.assertEqual(self.store.report()["observations"], 1)

    def test_malformed_envelopes(self):
        for body in (b"{", b"null", b"[]", b"{}", b'\xff'):
            with self.subTest(body=body):
                self.assertEqual(parse(body, NOW)["error"], "malformed_envelope")
        self.run_poll(Response(200, b"{"))
        self.assertEqual(self.store.report()["failed_polls"], 1)

    def test_invalid_rows_retained_as_quarantine(self):
        for field in ("LineRef", "VehicleLocation", "VehicleRef"):
            doc = json.loads(BODY)
            row = doc["Siri"]["ServiceDelivery"]["VehicleMonitoringDelivery"][0]["VehicleActivity"][0]
            del row["MonitoredVehicleJourney"][field]
            result = parse(json.dumps(doc).encode(), NOW)
            self.assertEqual(len(result["quarantine"]), 1)
            self.assertEqual(result["observations"], [])

    def test_empty_success(self):
        doc = json.loads(BODY)
        doc["Siri"]["ServiceDelivery"]["VehicleMonitoringDelivery"][0]["VehicleActivity"] = []
        self.run_poll(Response(200, json.dumps(doc).encode()))
        self.assertEqual(self.store.report()["successful_polls"], 1)
        self.assertEqual(self.store.report()["observations"], 0)

    def test_stale_and_future(self):
        self.assertIn("stale", parse(BODY, "2026-10-22T12:10:00Z")["observations"][0]["flags"])
        self.assertIn("future_source_time", parse(BODY, "2026-10-22T11:00:00Z")["observations"][0]["flags"])

    def test_duplicates_survive_restart(self):
        self.run_poll()
        self.run_poll()
        report = Store(self.store.root).report()
        self.assertEqual(report["persisted_polls"], 2)
        self.assertEqual(report["observations"], 2)
        self.assertEqual(report["duplicate_observations"], 1)
        self.assertEqual(report, Store(self.store.root).report())

    def test_failures_and_retry_bound(self):
        for status in (429, 503, 0):
            sleeps = []
            paths = self.run_poll(Response(status, b""), sleep=sleeps.append)
            self.assertEqual(len(paths), 3)
            self.assertEqual(sleeps, [2, 4])
        for status in (401, 403, 400):
            self.assertEqual(len(self.run_poll(Response(status, b""))), 1)

    def test_transport_exception_does_not_leak(self):
        def fail():
            raise TimeoutError("secret-url?key=NEVER_STORE")
        collect(self.store, fail, clock=lambda: NOW, sleep=lambda _: None)
        for p in self.store.bundles():
            self.assertNotIn("NEVER_STORE", (p / "metadata.json").read_text())
        self.assertEqual(self.store.report()["failed_attempts"], 3)
        self.assertEqual(self.store.report()["failed_polls"], 1)

    def test_retry_after(self):
        sleeps = []
        self.run_poll(Response(429, b"", "7"), sleep=sleeps.append)
        self.assertEqual(sleeps, [7, 7])
        self.assertEqual(len(self.run_poll(Response(429, b"", "120"))), 1)
        self.assertEqual(retry_delay("Thu, 22 Oct 2026 12:00:09 GMT", NOW, 2), 9)

    def test_secret_redaction(self):
        self.assertNotIn("dummy-secret", redact("error dummy-secret?key=dummy-secret", "dummy-secret"))
        self.run_poll(Response(200, b'{"echo":"dummy-secret"}'), secret="dummy-secret")
        path = self.store.bundles()[0]
        self.assertEqual((path / "response.bin").read_bytes(), b"")
        self.assertNotIn("dummy-secret", (path / "metadata.json").read_text())

    def test_secret_in_envelope_never_reaches_metadata(self):
        body = BODY.replace(b"2026-10-22T12:00:00Z", b"dummy-secret")
        self.run_poll(Response(200, body), secret="dummy-secret")
        for path in self.store.bundles():
            for file in path.iterdir():
                self.assertNotIn(b"dummy-secret", file.read_bytes())

    def test_crash_recovery(self):
        self.run_poll()
        for stage in ("response.bin", "metadata.json", "committed"):
            before = len(self.store.bundles())
            def crash(current):
                if current == stage:
                    raise OSError("simulated crash")
            broken = Store(self.store.root, fault=crash)
            with self.assertRaises(OSError):
                collect(broken, lambda: Response(200, BODY), clock=lambda: NOW)
            recovered = Store(self.store.root)
            self.assertEqual(len(recovered.bundles()), before + (stage == "committed"))
            self.assertEqual(recovered.report()["observations"], len(recovered.bundles()))
        path = self.store.bundles()[0]
        meta = json.loads((path / "metadata.json").read_text())
        with self.assertRaises(FileExistsError):
            self.store.save(meta, BODY)

    def test_provider_error(self):
        body = b'{"Siri":{"ServiceDelivery":{"VehicleMonitoringDelivery":[{"Status":false,"ErrorCondition":{}}]}}}'
        self.run_poll(Response(200, body))
        self.assertEqual(self.store.report()["failed_polls"], 1)

    def test_checksum_detects_corruption(self):
        path = self.run_poll()[0]
        (path / "response.bin").write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            self.store.report()


if __name__ == "__main__":
    unittest.main()
