"""API boundary: token verification, and proof the lecture API's import
closure never touches the legacy meeting/chat stack.

Tests that need FastAPI are skipped when it isn't installed (they run in any
real deployment/CI); the AST import-closure and token tests need nothing.
"""
import ast
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = "x" * 40

from server.auth_tokens import AuthError, create_token, verify_token  # noqa: E402  (backend on sys.path via conftest/pytest.ini)

try:
    import fastapi  # noqa: F401
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False


class TestTokens(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(verify_token(create_token(42, SECRET), SECRET), 42)

    def test_expired(self):
        t = create_token(1, SECRET, ttl_seconds=60, now=1000)
        self.assertEqual(verify_token(t, SECRET, now=1059), 1)
        with self.assertRaises(AuthError):
            verify_token(t, SECRET, now=1060)

    def test_wrong_secret_and_tampering(self):
        t = create_token(1, SECRET)
        with self.assertRaises(AuthError):
            verify_token(t, "y" * 40)
        h, p, s = t.split(".")
        import base64, json
        forged = base64.urlsafe_b64encode(json.dumps({"sub": "2", "exp": 9999999999}).encode()).rstrip(b"=").decode()
        with self.assertRaises(AuthError):
            verify_token(f"{h}.{forged}.{s}", SECRET)          # payload changed, signature kept

    def test_alg_none_and_garbage_rejected(self):
        import base64, json
        b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
        none_tok = f"{b({'alg': 'none'})}.{b({'sub': '1', 'exp': 9999999999})}."
        for bad in (none_tok, "", "a.b", "a.b.c", "not a token"):
            with self.assertRaises(AuthError, msg=bad):
                verify_token(bad, SECRET)

    def test_missing_or_bad_subject(self):
        import base64, hashlib, hmac, json
        def mint(payload):
            b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
            si = f"{b({'alg': 'HS256'})}.{b(payload)}"
            sig = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), si.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
            return f"{si}.{sig}"
        for payload in ({"exp": 9999999999}, {"sub": "abc", "exp": 9999999999}, {"sub": "1"}):
            with self.assertRaises(AuthError):
                verify_token(mint(payload), SECRET)

    def test_unconfigured_or_short_secret_fails_closed(self):
        t = create_token(1, SECRET)
        for s in ("", "short"):
            with self.assertRaises(AuthError):
                verify_token(t, s)
        with self.assertRaises(AuthError):
            create_token(1, "short")


FORBIDDEN_PREFIXES = ("server.api.routes_chat", "server.services", "server.schemas", "core.rag_engine",
                      "core.vector_store", "core.job_queue", "core.extractor", "core.db", "core.transcriber",
                      "langchain", "streamlit", "chromadb", "whisper")


def _local_module_file(mod: str):
    parts = mod.split(".")
    for base in (os.path.join(ROOT, "backend"), ROOT):
        p = os.path.join(base, *parts)
        if os.path.isfile(p + ".py"):
            return p + ".py"
        if os.path.isfile(os.path.join(p, "__init__.py")):
            return os.path.join(p, "__init__.py")
    return None


def _import_time_nodes(tree):
    """AST nodes that execute when the module is imported: everything except
    function bodies (lazy imports inside functions, e.g. qa.default_llm's
    optional LLM provider, only load when called)."""
    stack = list(tree.body)
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def import_closure(start: str):
    """Modules reachable by IMPORT-TIME import statements from `start`,
    following only repo-local files (server.*, core.*). Returns {module: imported_by}."""
    seen, queue = {}, [(start, None)]
    while queue:
        mod, by = queue.pop()
        if mod in seen:
            continue
        seen[mod] = by
        path = _local_module_file(mod)
        if not path:
            continue
        pkg = mod if path.endswith("__init__.py") else mod.rpartition(".")[0]
        for node in _import_time_nodes(ast.parse(open(path, encoding="utf-8").read())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    up = pkg.split(".")[: len(pkg.split(".")) - (node.level - 1)]
                    base = ".".join(up + ([node.module] if node.module else []))
                names = [base] + [f"{base}.{a.name}" for a in node.names]
            for n in names:
                if n.split(".")[0] in ("server", "core") or n.startswith(FORBIDDEN_PREFIXES):
                    queue.append((n, mod))
    return seen


class TestLectureApiIsolation(unittest.TestCase):
    def test_static_import_closure_excludes_legacy_stack(self):
        closure = import_closure("server.api.routes_lectures")
        self.assertIn("server.api.deps", closure)             # the dependency now resolves to a real file
        self.assertIsNotNone(_local_module_file("server.api.deps"))
        bad = {m: by for m, by in closure.items() if m.startswith(FORBIDDEN_PREFIXES)}
        self.assertEqual(bad, {}, f"lecture API reaches legacy modules: {bad}")

    def test_every_local_import_in_closure_exists(self):
        missing = [m for m in import_closure("server.api.routes_lectures")
                   if m.split(".")[0] in ("server", "core") and _local_module_file(m) is None
                   and _local_module_file(m.rpartition(".")[0] or m) is None]
        self.assertEqual(missing, [])

    def test_app_entrypoint_mounts_lecture_router_only(self):
        path = _local_module_file("server.main")
        self.assertIsNotNone(path, "backend/server/main.py is missing")
        src = open(path, encoding="utf-8").read()
        self.assertIn("app = FastAPI(", src)
        self.assertIn("app.include_router(routes_lectures.router)", src)
        closure = import_closure("server.main")
        self.assertIn("server.api.routes_lectures", closure)
        bad = {m: by for m, by in closure.items() if m.startswith(FORBIDDEN_PREFIXES)}
        self.assertEqual(bad, {}, f"app entrypoint reaches legacy modules: {bad}")

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed")
    def test_app_is_importable_and_serves_lecture_routes(self):
        code = ("import sys; from server.main import app; "
                "paths=set(app.openapi()['paths']); "  # openapi() flattens included routers; app.routes does not on newer FastAPI
                
                "need={'/lectures','/courses','/healthz','/lectures/{lecture_id}/viewer','/evidence/{evidence_id}'}; "
                "bad=[m for m in sys.modules if m.startswith(%r)]; "
                "print('OK' if need<=paths and not bad else (sorted(need-paths), bad))" % (FORBIDDEN_PREFIXES,))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": os.pathsep.join(
                               [os.path.join(ROOT, "backend"), ROOT] + [p for p in [os.environ.get("PYTHONPATH")] if p])})
        self.assertEqual(r.stdout.strip(), "OK", r.stderr[-500:])

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed")
    def test_real_import_does_not_load_legacy_modules(self):
        code = ("import sys; from server.api.routes_lectures import router; "
                "bad=[m for m in sys.modules if m.startswith(%r)]; print('OK' if router and not bad else bad)"
                % (FORBIDDEN_PREFIXES,))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": os.pathsep.join(
                               [os.path.join(ROOT, "backend"), ROOT] + [p for p in [os.environ.get("PYTHONPATH")] if p])})
        self.assertEqual(r.stdout.strip(), "OK", r.stderr[-500:])

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed")
    def test_get_current_user_fails_closed(self):
        from fastapi import HTTPException
        from server.api.deps import get_current_user
        # Exercise the dependency directly with a Request-shaped object. Calling
        # get_current_user(token) accidentally binds the token to `request` and
        # leaves FastAPI's Header(None) marker as the authorization value.
        from types import SimpleNamespace
        request = SimpleNamespace(
            headers=SimpleNamespace(get=lambda _key, default=None: default),
            client=SimpleNamespace(host="127.0.0.1"),
            method="GET", url=SimpleNamespace(path="/test"),
        )
        old = os.environ.pop("NOTIV_AUTH_SECRET", None)
        try:
            with self.assertRaises(HTTPException) as c:
                get_current_user(request, f"Bearer {create_token(1, SECRET)}")
            self.assertEqual(c.exception.status_code, 503)              # unconfigured -> never anonymous
            os.environ["NOTIV_AUTH_SECRET"] = SECRET
            self.assertEqual(get_current_user(request, f"Bearer {create_token(7, SECRET)}"), {"id": 7})
            for hdr in (None, "", "Bearer", "Basic abc", "Bearer garbage", f"Bearer {create_token(7, 'z' * 40)}"):
                with self.assertRaises(HTTPException) as c:
                    get_current_user(request, hdr)
                self.assertEqual(c.exception.status_code, 401, hdr)
        finally:
            os.environ.pop("NOTIV_AUTH_SECRET", None)
            if old:
                os.environ["NOTIV_AUTH_SECRET"] = old


if __name__ == "__main__":
    unittest.main()
