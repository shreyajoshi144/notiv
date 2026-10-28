"""Focused auth regression tests; run with the existing project test suite."""
import os, subprocess, sys, tempfile, textwrap
from pathlib import Path

def test_password_auth_cookie_csrf_logout_and_login():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env.update({"PYTHONPATH": f"{root / 'backend'}:{root}", "NOTIV_LECTURE_DB": str(Path(td) / "auth.db"),
                    "NOTIV_ENV": "development", "NOTIV_AUTH_SECRET": "unit-test-secret-longer-than-thirty-two-bytes"})
        code = textwrap.dedent('''
            from fastapi.testclient import TestClient
            from server.main import app
            c=TestClient(app)
            payload={"name":"Regression User","email":"auth-test@example.com","password":"test-password-long-enough","confirmation":"test-password-long-enough"}
            r=c.post('/auth/register',json=payload); assert r.status_code==200, r.text
            assert 'notiv_session' in c.cookies and 'notiv_csrf' in c.cookies
            assert 'HttpOnly' in r.headers.get('set-cookie','')
            assert c.get('/auth/password/me').status_code==200
            bad=c.post('/auth/password/logout'); assert bad.status_code==403, bad.text
            csrf=c.cookies.get('notiv_csrf')
            assert c.post('/auth/password/logout',headers={'X-CSRF-Token':csrf}).status_code==200
            assert c.get('/auth/password/me').status_code==401
            r=c.post('/auth/password/login',json={'email':payload['email'],'password':payload['password']}); assert r.status_code==200, r.text
            assert c.get('/auth/password/me').status_code==200
            assert c.post('/auth/password/login',json={'email':payload['email'],'password':'wrong-password'}).status_code==401
            assert c.post('/auth/register',json=payload).status_code==409
        ''')
        result=subprocess.run([sys.executable,"-c",code],cwd=root,env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode==0, result.stdout + "\n" + result.stderr
