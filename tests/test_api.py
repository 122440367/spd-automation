import json
import os
import sys
import threading
import types
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


if os.name == "nt":
    fake_fcntl = types.ModuleType("fcntl")
    fake_fcntl.LOCK_EX = 1
    fake_fcntl.LOCK_NB = 2
    fake_fcntl.flock = lambda *_args: None
    sys.modules.setdefault("fcntl", fake_fcntl)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spd_automation as app


REAL_DATETIME = app.datetime


class WorkerHandler(BaseHTTPRequestHandler):
    seen_paths = []

    def do_POST(self):
        self.seen_paths.append(self.path)
        assert self.headers.get("Authorization") == "Bearer test-token"
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/clear-uploaded-ips":
            self.respond({"success": True, "count": 0})
        elif self.path == "/upload-ips":
            assert b'name="ipfile"' in body
            assert b'name="maxIPs"' in body
            assert b"\r\n800\r\n" in body
            assert b"1.1.1.1" in body
            self.respond({"success": True, "count": 1, "addedCount": 1})
        elif self.path == "/challenge":
            self.send_response(403)
            self.send_header("cf-mitigated", "challenge")
            self.end_headers()
            self.wfile.write(b"challenge")
        else:
            self.respond({"success": False, "error": "not found"}, status=404)

    def respond(self, value, status=200):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), WorkerHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        app.URL = f"http://127.0.0.1:{cls.server.server_port}/"
        app.API_TOKEN = "test-token"
        app.MAX_PENDING_IPS = 800
        app.STEP_DELAY_SECONDS = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_worker_upload_api_sequence(self):
        csv_path = Path(__file__).with_name("fixture.csv")
        self.assertEqual(app.clear_uploaded_ips()["count"], 0)
        self.assertEqual(app.upload_csv(csv_path)["count"], 1)

    def test_main_only_clears_and_uploads_to_worker(self):
        csv_path = Path(__file__).with_name("fixture.csv")
        WorkerHandler.seen_paths.clear()
        with (
            patch.object(app, "todays_csv", return_value=[csv_path]),
            patch.object(app, "wait_for_stable_file"),
            patch.object(app, "acquire_job_lock"),
            patch.object(app, "send_telegram"),
        ):
            self.assertEqual(app.main(), 0)
        self.assertEqual(
            WorkerHandler.seen_paths,
            ["/clear-uploaded-ips", "/upload-ips"],
        )

    def test_todays_csv_selects_all_csv_files_from_today(self):
        fixture = Path(__file__).with_name("fixture.csv")

        class FixtureDateTime:
            @classmethod
            def now(cls):
                return REAL_DATETIME.fromtimestamp(fixture.stat().st_mtime)

            @classmethod
            def fromtimestamp(cls, timestamp):
                return REAL_DATETIME.fromtimestamp(timestamp)

        class FixtureDirectory:
            def is_dir(self):
                return True

            def iterdir(self):
                return iter([fixture])

        original = app.datetime
        try:
            app.datetime = FixtureDateTime
            self.assertEqual([path.name for path in app.todays_csv(FixtureDirectory())], ["fixture.csv"])
        finally:
            app.datetime = original

    def test_cloudflare_challenge_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "Cloudflare Challenge"):
            app.request_json("/challenge", method="POST", body=b"", timeout=2)


if __name__ == "__main__":
    unittest.main()
