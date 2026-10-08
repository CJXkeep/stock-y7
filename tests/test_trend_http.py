"""HTTP integration without starting any watchers or contacting market sources."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from server import trend_service


class TrendHttpTests(unittest.TestCase):
    def test_readonly_get_creation_isolation_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(trend_service, "TREND_ROOT", os.path.join(tmp, "new")), \
                patch.object(app, "AUTH_ENABLED", False):
            server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("GET", "/api/trend")
                data = json.loads(conn.getresponse().read())
                self.assertFalse(data["exists"])
                self.assertFalse(os.path.exists(os.path.join(tmp, "new")))
                conn.request("GET", "/trend.html")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn("中期趋势组合", response.read().decode("utf-8"))
                conn.request("POST", "/api/trend", json.dumps({"action": "create", "root": os.path.join(tmp, "escaped")}),
                             {"Content-Type": "application/json"})
                created = json.loads(conn.getresponse().read())
                self.assertTrue(created["ok"])
                self.assertFalse(created["enabled"])
                self.assertFalse(os.path.exists(os.path.join(tmp, "escaped")))
                conn.request("GET", "/api/trend")
                data = json.loads(conn.getresponse().read())
                self.assertEqual(data["state"]["cash"], 100000)
                self.assertFalse(data["enabled"])
                with patch.object(app, "AUTH_ENABLED", True):
                    conn.request("GET", "/api/trend")
                    response = conn.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()
                    conn.request("POST", "/api/trend", '{"action":"enable"}', {"Content-Type": "application/json"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()
            finally:
                conn.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
