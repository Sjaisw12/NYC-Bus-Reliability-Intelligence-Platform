"""Focused regression coverage; all credentials and responses are synthetic."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mta_collector import cli
from mta_collector.core import (DATA_ROOT, ENDPOINT, LiveTransport, Response, Store,
                                collect, credential_query)

NOW = "2026-10-22T12:00:00Z"
BODY = (Path(__file__).parent / "fixtures/vehicle.json").read_bytes()


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "polls")
        block = patch.object(socket, "socket", side_effect=AssertionError("network forbidden"))
        block.start()
        self.addCleanup(block.stop)

    def collect(self, store=None, body=BODY, **kwargs):
        return collect(store or self.store, lambda: Response(200, body),
                       clock=lambda: NOW, **kwargs)

    def test_accepted_and_quarantine_are_separate_with_lineage(self):
        doc = json.loads(BODY)
        activities = doc["Siri"]["ServiceDelivery"]["VehicleMonitoringDelivery"][0]["VehicleActivity"]
        bad = json.loads(json.dumps(activities[0]))
        del bad["MonitoredVehicleJourney"]["VehicleLocation"]
        activities.append(bad)
        body = json.dumps(doc).encode()
        raw = self.collect(body=body)[0]
        self.assertEqual((raw / "response.bin").read_bytes(), body)
        derived = self.store.root / "derived" / raw.name
        accepted = json.loads((derived / "accepted.jsonl").read_text())
        quarantine = json.loads((derived / "quarantine.jsonl").read_text())
        self.assertEqual(accepted["ordinal"], 0)
        self.assertEqual(quarantine["ordinal"], 1)
        for row in (accepted, quarantine):
            self.assertEqual(row["raw_path"], str(raw / "response.bin"))
            self.assertEqual(row["recorded_at_utc"], "2026-10-22T11:59:30+00:00")
            self.assertEqual(row["ingested_at_utc"], NOW)
            self.assertEqual(row["payload_sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(row["observation_id"], row["attempt_id"] + ":" + str(row["ordinal"]))
        self.assertEqual(self.store.report()["quarantined_rows"], 1)
        self.assertFalse((raw / "accepted.jsonl").exists())

    def test_derived_recovery_at_every_commit_boundary_is_idempotent(self):
        for stage in ("committed", "accepted.jsonl", "quarantine.jsonl", "derived_committed"):
            with self.subTest(stage=stage):
                root = self.root / stage
                def crash(current):
                    if stage == current:
                        raise OSError("synthetic crash")
                with self.assertRaises(OSError):
                    self.collect(Store(root, fault=crash))
                recovered = Store(root)
                raw = recovered.bundles()[0]
                original = (raw / "response.bin").read_bytes()
                recovered.recover()
                target = root / "derived" / raw.name
                before = {p.name: p.read_bytes() for p in target.iterdir()}
                recovered.recover()
                self.assertEqual(before, {p.name: p.read_bytes() for p in target.iterdir()})
                self.assertEqual(recovered.report()["observations"], 1)
                self.assertEqual((raw / "response.bin").read_bytes(), original)

    def test_derived_corruption_is_detected_not_overwritten(self):
        raw = self.collect()[0]
        path = self.store.root / "derived" / raw.name / "accepted.jsonl"
        path.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.store.recover()
        self.assertEqual(path.read_bytes(), b"corrupt")

    def test_retry_success_is_one_logical_poll_and_two_attempts(self):
        responses = iter([Response(503, b""), Response(200, BODY)])
        paths = collect(self.store, lambda: next(responses), clock=lambda: NOW, sleep=lambda _: None)
        metas = [json.loads((p / "metadata.json").read_text()) for p in paths]
        self.assertEqual(metas[0]["poll_id"], metas[1]["poll_id"])
        self.assertNotEqual(metas[0]["attempt_id"], metas[1]["attempt_id"])
        report = self.store.report()
        self.assertEqual((report["persisted_polls"], report["successful_polls"], report["failed_polls"]), (1, 1, 0))
        self.assertEqual((report["http_attempts"], report["failed_attempts"], report["successful_attempts"]), (2, 1, 1))
        self.assertEqual(report["incomplete_polls"], 0)

    def test_interrupted_retry_is_incomplete_not_failed_poll(self):
        def interrupted(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            collect(self.store, lambda: Response(503, b""), clock=lambda: NOW, sleep=interrupted)
        report = self.store.report()
        self.assertEqual(report["failed_attempts"], 1)
        self.assertEqual(report["failed_polls"], 0)
        self.assertEqual(report["incomplete_polls"], 1)

    def test_legacy_raw_bundle_recovers_without_editing_metadata(self):
        meta = {"poll_id": "a" * 32, "mode": "fixture", "error": None,
                "ingested_at_utc": NOW, "bytes": len(BODY),
                "payload_sha256": hashlib.sha256(BODY).hexdigest()}
        raw = self.store.save(meta, BODY)
        before = (raw / "metadata.json").read_bytes()
        self.assertEqual(self.store.report()["observations"], 1)
        self.assertEqual(before, (raw / "metadata.json").read_bytes())

    def test_synthetic_placeholder_preserved_in_mock_request(self):
        token = "SYNTHETIC_PLACEHOLDER-123_~."
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.status, response.read.return_value = 200, BODY
        with patch.dict(os.environ, {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener", return_value=opener):
            transport = LiveTransport(enabled=True)
            transport()
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(ENDPOINT + "?"))
        self.assertTrue(request.full_url.endswith("&key=" + token))
        self.assertIn("LineRef=MTA+NYCT_B63", request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)

    def test_unsupported_placeholder_fails_closed_without_value_in_error(self):
        for token in ("SYNTHETIC<TOKEN>", "SYNTHETIC&TOKEN", "SYNTHETIC%TOKEN", "SYNTHETIC TOKEN", "SYNTHETIC\nTOKEN"):
            with self.subTest(token=token), patch.dict(os.environ, {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener") as build:
                with self.assertRaises(ValueError) as exc:
                    LiveTransport(enabled=True)
                self.assertNotIn(token, str(exc.exception))
                build.assert_not_called()

    def test_transport_errors_never_persist_synthetic_placeholder(self):
        token = "SYNTHETIC_PLACEHOLDER"
        opener = MagicMock()
        opener.open.side_effect = OSError("query?key=" + token)
        with patch.dict(os.environ, {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener", return_value=opener):
            collect(self.store, LiveTransport(enabled=True), clock=lambda: NOW,
                    secret=token, sleep=lambda _: None)
        for file in self.store.root.rglob("*"):
            if file.is_file():
                self.assertNotIn(token.encode(), file.read_bytes())

    def test_unknown_raw_credential_shape_is_rejected_without_knowing_key(self):
        fake = "-".join(["0"*8, "0"*4, "0"*4, "0"*4, "0"*12])
        self.collect(body=json.dumps({"echo": fake}).encode(), secret="SYNTHETIC_PLACEHOLDER")
        raw = self.store.bundles()[0]
        self.assertEqual((raw / "response.bin").read_bytes(), b"")
        self.assertEqual(json.loads((raw / "metadata.json").read_text())["error"], "unsafe_credential_echo")

    def test_storage_rejects_unignored_paths_traversal_and_symlinks(self):
        for path in (DATA_ROOT.parent / "output", DATA_ROOT / ".." / "custom", Path("/workspace/custom")):
            with self.assertRaises(ValueError):
                Store(path)
        link = self.root / "link"
        link.symlink_to(self.root / "polls", target_is_directory=True)
        with self.assertRaises(ValueError):
            Store(link / "nested")
        self.assertTrue(Store(DATA_ROOT / "correction-test").root.is_relative_to(DATA_ROOT))

    def test_recovery_rejects_symlinked_derived_bundle(self):
        raw = self.collect()[0]
        target = self.store.root / "derived" / raw.name
        moved = self.root / "moved-derived"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.recover()

    def test_cli_rejects_output_before_transport(self):
        with patch("sys.argv", ["collector", "--live", "--output", str(DATA_ROOT.parent / "unsafe")]), patch("mta_collector.cli.LiveTransport") as transport:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                cli.main()
            self.assertEqual(exc.exception.code, 2)
            transport.assert_not_called()

    def test_ignored_artifacts_but_docs_and_synthetic_fixture_visible(self):
        for name in ("data/test/response.bin", "custom/poll-example/response.bin", ".tmp-example/raw", "credentials.json", "secrets/token", "reports/health.json", "review.zip", "custom/accepted.jsonl"):
            self.assertEqual(subprocess.run(["git", "check-ignore", "--no-index", "-q", name]).returncode, 0, name)
        for name in ("docs/data-contract.md", "tests/fixtures/vehicle.json", ".env.example"):
            self.assertEqual(subprocess.run(["git", "check-ignore", "--no-index", "-q", name]).returncode, 1, name)

    def test_cli_successful_retry_exits_success_and_reports_attempts(self):
        def fixture_retry(store, transport, **kwargs):
            responses = iter([Response(503, b""), Response(200, BODY)])
            return collect(store, lambda: next(responses), clock=lambda: NOW, sleep=lambda _: None)
        with patch("sys.argv", ["collector", "--fixture", str(Path(__file__).parent / "fixtures/vehicle.json"), "--output", str(self.root / "cli")]), patch("mta_collector.cli.collect", side_effect=fixture_retry):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                cli.main()
        health = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(health["successful_polls"], 1)
        self.assertEqual(health["http_attempts"], 2)
