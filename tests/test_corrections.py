"""Focused regression coverage; all credentials and responses are synthetic."""
import contextlib
from email.message import Message
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
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import ProxyHandler, build_opener

from mta_collector import cli
from mta_collector.core import (DATA_ROOT, ENDPOINT, LiveTransport, Response, Store,
                                NoRedirect, collect, credential_query, redact)

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
        self.assertIn("LineRef=MTA%20NYCT_B63", request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)

    def test_query_encoding_round_trips_and_isolates_fixed_parameters(self):
        for token in ("SYNTHETIC<TOKEN>", "%", "%26key%3Dother", "+", "&", "?", "#",
                      " ", "SYNTHETIC café 雪", "version=3&LineRef=OTHER&VehicleMonitoringDetailLevel=calls&key=other#fragment"):
            with self.subTest(token=token):
                query = credential_query(token)
                parts = urlsplit(ENDPOINT + "?" + query)
                self.assertEqual(parts.fragment, "")
                self.assertNotIn("+", query)
                self.assertNotIn(" ", query)
                pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
                self.assertEqual(pairs, [("version", "2"), ("LineRef", "MTA NYCT_B63"),
                                        ("VehicleMonitoringDetailLevel", "normal"), ("key", token)])
                self.assertTrue(query.endswith("&key=" + quote(token, safe="")))

    def test_empty_controls_and_surrogates_fail_closed_without_value_in_error(self):
        tokens = [""] + ["SYNTHETIC" + chr(c) + "TOKEN" for c in
                          [*range(32), *range(127, 160), 0xD800, 0xDFFF]]
        for token in tokens:
            with self.subTest(token=token), patch("mta_collector.core.os.environ", {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener") as build:
                with self.assertRaisesRegex(ValueError, "^credential is empty or contains unsupported characters$"):
                    credential_query(token)
                with self.assertRaises(ValueError) as exc:
                    LiveTransport(enabled=True)
                self.assertIn(str(exc.exception), ("MTA_BUSTIME_API_KEY is required",
                                                  "credential is empty or contains unsupported characters"))
                build.assert_not_called()

    def test_encoded_synthetic_value_in_mock_request_preserves_transport_policy(self):
        token = "SYNTHETIC%+&?# café"
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.status, response.read.return_value = 200, BODY
        with patch.dict(os.environ, {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener", return_value=opener) as build:
            transport = LiveTransport(enabled=True)
            self.assertEqual(transport(), Response(200, BODY, response.headers.get("Retry-After")))
        handlers = build.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], ProxyHandler)
        self.assertIsInstance(handlers[1], NoRedirect)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, ENDPOINT + "?" + credential_query(token))
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 15})

    def test_redirects_refused_without_second_request(self):
        # Exercise urllib's redirect handling offline, including the Location header.
        redirect = NoRedirect()
        opener = build_opener(ProxyHandler({}), redirect)
        token = "SYNTHETIC&?#"
        with patch.dict(os.environ, {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener", return_value=opener):
            transport = LiveTransport(enabled=True)
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                def redirected(request, **kwargs):
                    headers = Message()
                    headers["Location"] = "https://example.invalid/redirect"
                    return opener.error("https", request, io.BytesIO(), status, "redirect", headers)
                with patch.object(opener, "open", side_effect=redirected) as opened:
                    self.assertEqual(transport().status, status)
                    opened.assert_called_once()

    def test_raw_and_encoded_credential_echoes_rejected_and_redacted(self):
        token = "SYNTHETIC%+&?# café"
        for echo in (token, quote(token, safe="")):
            with self.subTest(echo=echo):
                self.assertEqual(redact("echo " + echo, token), "echo [REDACTED]")
                raw = self.collect(body=json.dumps({"echo": echo}, ensure_ascii=False).encode(), secret=token)[0]
                self.assertEqual((raw / "response.bin").read_bytes(), b"")
                self.assertEqual(json.loads((raw / "metadata.json").read_text())["error"], "unsafe_credential_echo")
        for file in self.store.root.rglob("*"):
            if file.is_file():
                self.assertNotIn(token.encode(), file.read_bytes())
                self.assertNotIn(quote(token, safe="").encode(), file.read_bytes())

    def test_credential_urls_never_exposed_by_transport_errors_or_logs(self):
        token = "SYNTHETIC%+&?# café"
        url = ENDPOINT + "?" + credential_query(token)
        for failure in (OSError(url), HTTPError(url, 403, url, {}, None)):
            opener = MagicMock()
            opener.open.side_effect = failure
            with patch.dict(os.environ, {"MTA_BUSTIME_API_KEY": token}), patch("mta_collector.core.build_opener", return_value=opener):
                transport = LiveTransport(enabled=True)
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr, patch("logging.Logger._log") as log:
                if isinstance(failure, HTTPError):
                    self.assertEqual(transport(), Response(403, b""))
                else:
                    with self.assertRaises(OSError) as exc:
                        transport()
                    self.assertEqual(str(exc.exception), "transport failure")
                    self.assertTrue(exc.exception.__suppress_context__)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                log.assert_not_called()

    def test_disabled_live_mode_does_not_read_credential(self):
        with patch("mta_collector.core.os.environ.get", side_effect=AssertionError("must not read environment")), patch("mta_collector.core.build_opener") as build:
            with self.assertRaisesRegex(ValueError, "explicit authorization required"):
                LiveTransport()
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
