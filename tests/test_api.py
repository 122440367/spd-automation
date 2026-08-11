import json
import os
import sys
import threading
import types
import unittest
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


class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/upload-ips":
            assert b'name="ipfile"' in body
            assert b"1.1.1.1" in body
            self.respond({"success": True, "count": 1})
        elif self.path == "/manual-speedtest":
            assert json.loads(body) == {"maxTests": 25}
            self.respond(
                {
                    "success": True,
                    "tested": 1,
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
        app.USERNAME = ""
        app.PASSWORD = ""
        app.MAX_TESTS = 25

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_complete_api_sequence(self):
        csv_path = Path(__file__).with_name("fixture.csv")
        self.assertIn("成功解析 1 个 IP", app.upload_csv(csv_path))
        self.assertIn("测试完成", app.run_speedtest())
        self.assertIn("owner/repo", app.upload_to_github())

    def test_cloudflare_challenge_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "Cloudflare Challenge"):
            app.request_json("/challenge", method="POST", body=b"", timeout=2)


if __name__ == "__main__":
    unittest.main()
