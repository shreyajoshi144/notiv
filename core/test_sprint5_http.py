"""Sprint 5 HTTP regression/security tests over REAL HTTP against server.dev_server (stdlib server; the FastAPI app is
NOT exercised here). Includes regression tests for the two bugs found earlier:
  BUG-1  oversized/zero ids -> HTTP 500 (OverflowError)          -> must be 404
  BUG-2  missing required field in a POST body -> HTTP 500 (TypeError) -> must be 422
"""
import contextlib
import os
import unittest
import urllib.error
import urllib.request
from unittest import mock

from core.lecture import obs, ratelimit
from core.test_study_api import ALICE, BOB, W, ApiBase

HUGE = "99999999999999999999999"


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: str(v) for k, v in kv.items()})
    ratelimit.reset()
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        ratelimit.reset()


class TestBug1OversizedIds(ApiBase):
    def test_huge_and_zero_ids_are_404_not_500(self):
        for path in (f"/lectures/{HUGE}/overview", f"/lectures/{HUGE}/knowledge", f"/lectures/0/overview",
                     f"/lectures/{self.lid}/evidence/{HUGE}", f"/study/items/{HUGE}", f"/study/sessions/{HUGE}",
                     f"/courses/{HUGE}/overview", f"/lectures/{HUGE}/study/items"):
            code, body, _ = self.call("GET", path)
            self.assertEqual(code, 404, f"{path} -> {code} {body}")
            self.assertEqual(body["detail"], "not found")

    def test_huge_ids_on_mutations(self):
        self.assertEqual(self.call("DELETE", f"/knowledge/{HUGE}")[0], 404)
        self.assertEqual(self.call("POST", f"/knowledge/{HUGE}/review", {})[0], 404)
        self.assertEqual(self.call("POST", f"/jobs/{HUGE}/retry", {})[0], 404)
        self.assertEqual(self.call("POST", f"/lectures/{HUGE}/chat", {"question": "hi"})[0], 404)


class TestBug2MissingFields(ApiBase):
    def test_missing_required_field_is_422_not_500(self):
        sid = self.call("POST", f"/lectures/{self.lid}/study/sessions", {"limit": 2})[1]["session_id"]
        code, body, _ = self.call("POST", f"/study/sessions/{sid}/attempts", {})
        self.assertEqual(code, 422)
        self.assertIn("missing fields", body["detail"])
        self.assertIn("item_id", body["detail"])

    def test_wrong_type_is_422(self):
        code, body, _ = self.call("POST", f"/lectures/{self.lid}/study/sessions", {"limit": "ten"})
        self.assertEqual(code, 422, body)
        code, _, _ = self.call("POST", f"/lectures/{self.lid}/study/generate", {"kinds": "flashcard"})
        self.assertEqual(code, 422)

    def test_unknown_field_still_422_and_valid_body_still_ok(self):
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/study/generate", {"nope": 1})[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/study/sessions", {"limit": 2})[0], 200)


class TestHardening(ApiBase):
    def test_security_headers_and_request_id(self):
        code, _, h = self.call("GET", f"/lectures/{self.lid}/knowledge")
        self.assertEqual(code, 200)
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertRegex(h["X-Request-ID"], r"^[A-Za-z0-9._-]{8,64}$")

    def test_request_id_is_propagated_only_when_well_formed(self):
        def hit(rid):
            req = urllib.request.Request(self.base + "/healthz", headers={"X-Request-ID": rid})
            with urllib.request.urlopen(req) as r:
                return r.headers["X-Request-ID"]
        self.assertEqual(hit("trace-abcdef123"), "trace-abcdef123")
        self.assertNotEqual(hit("bad id with spaces!!"), "bad id with spaces!!")

    def test_unhandled_error_is_generic_with_request_id(self):
        with mock.patch("core.lecture.workspace.WorkspaceService.overview", side_effect=RuntimeError("secret /home/x/db password=hunter2")):
            code, body, h = self.call("GET", f"/lectures/{self.lid}/overview")
        self.assertEqual(code, 500)
        self.assertEqual(body, {"detail": "internal server error"})          # EXACT body: no request_id, nothing else
        self.assertRegex(h["X-Request-ID"], r"^[A-Za-z0-9._-]{8,64}$")       # the id lives in the header only
        self.assertNotIn("hunter2", str(body)); self.assertNotIn("/home/x", str(body)); self.assertNotIn("RuntimeError", str(body))

    def test_bad_content_length_and_oversize(self):
        import http.client
        host, port = self.base.replace("http://", "").split(":")
        c = http.client.HTTPConnection(host, int(port))
        c.putrequest("POST", f"/lectures/{self.lid}/study/generate"); c.putheader("Authorization", "Bearer " + self.tok[ALICE])
        c.putheader("Content-Length", "abc"); c.endheaders()
        self.assertEqual(c.getresponse().status, 400)
        c = http.client.HTTPConnection(host, int(port))
        c.putrequest("POST", f"/lectures/{self.lid}/study/generate"); c.putheader("Authorization", "Bearer " + self.tok[ALICE])
        c.putheader("Content-Length", str(10 ** 7)); c.endheaders()
        self.assertEqual(c.getresponse().status, 413)

    def test_invalid_json_422(self):
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/study/generate", raw=b"{not json")[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/study/generate", raw=b"[1,2]")[0], 422)

    def test_rate_limit_per_user_with_retry_after(self):
        with env(NOTIV_RL_READ="3/60"):
            codes = [self.call("GET", f"/lectures/{self.lid}/knowledge")[0] for _ in range(5)]
            self.assertEqual(codes[:3], [200, 200, 200]); self.assertEqual(codes[3:], [429, 429])
            code, body, h = self.call("GET", f"/lectures/{self.lid}/knowledge")
            self.assertGreaterEqual(int(h["Retry-After"]), 1)
            self.assertEqual(self.call("GET", f"/lectures/{self.lid}/knowledge", user=BOB)[0], 404)   # other user: own bucket

    def test_limiter_store_outage_deny_mode_is_503_with_retry_after(self):
        from core.lecture import ops_store
        ratelimit.reset_state()
        with env(NOTIV_RATELIMIT_BACKEND="db", NOTIV_RATELIMIT_ON_ERROR="deny"):
            with mock.patch.object(ops_store, "rl_hit", side_effect=RuntimeError("db down password=hunter2")):
                code, body, h = self.call("GET", f"/lectures/{self.lid}/knowledge")
        self.assertEqual(code, 503)
        self.assertIn("Retry-After", h); self.assertNotIn("hunter2", str(body))
        ratelimit.reset_state()

    def test_limiter_store_outage_default_still_limits_per_process(self):
        from core.lecture import ops_store
        ratelimit.reset_state()
        with env(NOTIV_RATELIMIT_BACKEND="db", NOTIV_RL_READ="2/60"):
            with mock.patch.object(ops_store, "rl_hit", side_effect=RuntimeError("db down")):
                codes = [self.call("GET", f"/lectures/{self.lid}/knowledge")[0] for _ in range(4)]
        self.assertEqual(codes, [200, 200, 429, 429])
        ratelimit.reset_state()

    def test_auth_failures_are_throttled_per_ip(self):
        with env(NOTIV_RL_AUTH_FAIL="2/300"):
            def bad():
                req = urllib.request.Request(self.base + f"/lectures/{self.lid}/knowledge", headers={"Authorization": "Bearer nope"})
                try:
                    urllib.request.urlopen(req)
                except urllib.error.HTTPError as e:
                    return e.code
            self.assertEqual([bad() for _ in range(4)], [401, 401, 429, 429])

    def test_cross_user_cannot_retry_or_read(self):
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/overview", user=BOB)[0], 404)
        self.assertEqual(self.call("POST", "/jobs/1/retry", {}, user=BOB)[0], 404)

    def test_ask_quota(self):
        lid = W.new_lecture(ALICE, "quota lecture", *self._quota_args())
        with env(NOTIV_QUOTA_ASKS_PER_DAY="1"):
            self.assertEqual(self.call("POST", f"/lectures/{lid}/chat", {"question": "What is gradient descent?"})[0], 200)
            code, body, _ = self.call("POST", f"/lectures/{lid}/chat", {"question": "And the learning rate?"})
            self.assertEqual((code, body["detail"]["code"]), (429, "quota_exceeded"))

    def _quota_args(self):
        from core.test_study import F
        return F.DL1, F.DL1_SLIDES, W.c_scratch


class TestOpsEndpoints(ApiBase):
    def test_healthz_readyz(self):
        self.assertEqual(self.call("GET", "/healthz", user=None)[0], 200)
        code, body, _ = self.call("GET", "/readyz", user=None)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "ready")
        self.assertTrue(body["checks"]["database"]["ok"] and body["checks"]["storage"]["ok"])

    def test_readyz_503_when_auth_unconfigured(self):
        with env(NOTIV_AUTH_SECRET=""):
            code, body, _ = self.call("GET", "/readyz", user=None)
        self.assertEqual((code, body["status"]), (503, "not_ready"))
        self.assertNotIn(os.environ["NOTIV_AUTH_SECRET"], str(body))

    def test_metrics_gated(self):
        self.assertEqual(self.call("GET", "/metrics", user=None)[0], 404)                       # disabled by default
        with env(NOTIV_METRICS_ENABLED="1", NOTIV_METRICS_TOKEN="m" * 24):
            def get(tok):
                req = urllib.request.Request(self.base + "/metrics", headers={"Authorization": "Bearer " + tok})
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, r.read().decode()
                except urllib.error.HTTPError as e:
                    return e.code, ""
            self.assertEqual(get("wrong")[0], 404)
            code, text = get("m" * 24)
            self.assertEqual(code, 200); self.assertIn("notiv_http_requests_total", text)

    def test_signed_files_defensive(self):
        for q in ("exp=abc&sig=zz", "exp=1&sig=zz", "", "exp=99999999999999999999&sig=x", f"exp={2**70}&sig=x"):
            self.assertEqual(self.call("GET", f"/files/lectures/1/a.png?{q}", user=None)[0], 403, q)

    def test_signed_file_served_with_safe_headers(self):
        sl = self.call("GET", f"/lectures/{self.lid}/slides")[1]
        url = (sl["slides"] if isinstance(sl, dict) else sl)[0]["image_url"]
        with urllib.request.urlopen(self.base + url) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(r.headers["Content-Disposition"], "inline")
        bad = url.replace("sig=", "sig=0")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + bad)
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main()


class TestServerCleanup(unittest.TestCase):
    """The test server must stop cleanly: no lingering request threads / open sockets, and a process that used it
    (e.g. pytest) must be able to EXIT after reporting results even if a client left a keep-alive connection open."""

    def _start(self):
        import threading
        from server.dev_server import make_server
        srv = make_server(0)
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        return srv, t

    def test_shutdown_closes_socket_threads_and_idle_keepalive_connections(self):
        import http.client
        import threading
        import time
        before = threading.active_count()
        srv, t = self._start()
        port = srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/healthz"); self.assertEqual(c.getresponse().status, 200)       # connection stays open (keep-alive)
        srv.shutdown(); srv.shutdown()                                                    # idempotent
        t.join(5); self.assertFalse(t.is_alive())
        deadline = time.time() + 5
        while threading.active_count() > before and time.time() < deadline:
            time.sleep(0.05)
        self.assertLessEqual(threading.active_count(), before)                            # handler thread for the idle conn is gone
        with self.assertRaises(OSError):
            http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/healthz")     # listener closed
        c.close()

    def test_shutdown_before_serve_loop_started_does_not_hang(self):
        from server.dev_server import make_server
        srv = make_server(0)
        srv.shutdown()                                                                    # never served: must return promptly

    def test_process_exits_with_open_keepalive_connection(self):
        import subprocess
        import sys
        import textwrap
        code = textwrap.dedent("""
            import http.client, threading
            from server.dev_server import make_server
            srv = make_server(0)
            threading.Thread(target=srv.serve_forever).start()           # NON-daemon serve thread, like a naive harness
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("GET", "/healthz"); c.getresponse().read()         # leave the connection open
            srv.shutdown()
            print("done")
        """)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([root, os.path.join(root, "core"), os.path.join(root, "backend")]))
        r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "done"), r.stderr)
