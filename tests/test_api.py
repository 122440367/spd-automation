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
    def do_POST(self):
        assert self.headers.get("Authorization") == "Bearer test-token"
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/clear-uploaded-ips":
            self.respond({"success": True, "count": 0})
        elif self.path == "/upload-ips":
            assert b'name="ipfile"' in body
            assert b'name="maxIPs"' in body
            assert b"1.1.1.1" in body
            self.respond({"success": True, "count": 1, "addedCount": 1})
        elif self.path == "/manual-speedtest":
            assert json.loads(body) == {"maxTests": 20, "offset": 0}
            self.respond(
                {
                    "success": True,
                    "tested": 1,
                    "batchTested": 1,
                    "nextOffset": 1,
                    "total": 1,
                    "complete": True,
                    "source": "uploaded",
                    "duration": "100ms",
                }
            )
        elif self.path == "/upload-to-github":
            self.respond(
                {
                    "success": True,
                    "count": 1,
                    "repo": "owner/repo",
                    "file": "niceip.txt",
                }
            )
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
        app.MAX_PENDING_IPS = 300
        app.SPEEDTEST_BATCH_SIZE = 20
        app.STEP_DELAY_SECONDS = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_complete_api_sequence(self):
        csv_path = Path(__file__).with_name("fixture.csv")
        self.assertEqual(app.clear_uploaded_ips()["count"], 0)
        self.assertEqual(app.upload_csv(csv_path)["count"], 1)
        self.assertIn("测速完成", app.run_speedtest(1))
        self.assertIn("owner/repo", app.upload_to_github())

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

    def test_speedtest_runs_all_batches(self):
        offsets = []

        def fake_request(_path, **kwargs):
            payload = json.loads(kwargs["body"])
            offsets.append(payload["offset"])
            next_offset = min(payload["offset"] + payload["maxTests"], 45)
            return {
                "success": True,
                "batchTested": next_offset - payload["offset"],
                "nextOffset": next_offset,
            }

        with patch.object(app, "request_json", side_effect=fake_request):
            self.assertIn("分 3 批", app.run_speedtest(45))
        self.assertEqual(offsets, [0, 20, 40])

    def test_cloudflare_challenge_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "Cloudflare Challenge"):
            app.request_json("/challenge", method="POST", body=b"", timeout=2)


if __name__ == "__main__":
    unittest.main()
