"""End-to-end cookie-session integration: browser-equivalent requests across app APIs."""
import os, subprocess, sys, tempfile, textwrap
from pathlib import Path


def _run(code):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env.update({"PYTHONPATH": f"{root / 'backend'}:{root}", "NOTIV_LECTURE_DB": str(Path(td) / "cookie-auth.db"),
                    "NOTIV_ENV": "development", "NOTIV_AUTH_SECRET": "cookie-integration-secret-at-least-32-bytes"})
        result = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=root, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_registration_cookie_session_workspace_refresh_logout_and_isolation():
    _run('''
        from fastapi.testclient import TestClient
        from server.main import app
        a=TestClient(app); b=TestClient(app)
        payload={"name":"Alice","email":"alice-cookie@example.com","password":"long-secure-password-123","confirmation":"long-secure-password-123"}
        r=a.post('/auth/register',json=payload); assert r.status_code==200,r.text
        assert 'notiv_session' in a.cookies and 'notiv_csrf' in a.cookies
        assert 'httponly' in r.headers.get('set-cookie','').lower()
        assert a.get('/auth/password/me').status_code==200
        assert a.get('/notebooks').status_code==200
        csrf=a.cookies.get('notiv_csrf')
        no_csrf=a.post('/notebooks',json={"title":"should fail"}); assert no_csrf.status_code==403,no_csrf.text
        cross_site=a.post('/notebooks',headers={'X-CSRF-Token':csrf,'Origin':'https://evil.example'},json={"title":"cross site"}); assert cross_site.status_code==403,cross_site.text
        nb=a.post('/notebooks',headers={'X-CSRF-Token':csrf},json={"title":"Alice private notebook"})
        assert nb.status_code==201,nb.text
        assert a.get('/notebooks/'+str(nb.json()['id'])).status_code==200
        lecture=a.post('/lectures',headers={'X-CSRF-Token':csrf},json={"title":"Alice lecture"})
        assert lecture.status_code==201,lecture.text
        lid=lecture.json()['lecture_id']; assert a.get('/lectures/'+str(lid)).status_code==200
        assert b.get('/notebooks/'+str(nb.json()['id'])).status_code==401
        p2={"name":"Bob","email":"bob-cookie@example.com","password":"another-secure-password-456","confirmation":"another-secure-password-456"}
        assert b.post('/auth/register',json=p2).status_code==200
        assert b.get('/notebooks/'+str(nb.json()['id'])).status_code==404
        # Page refresh/new request reuses the same HttpOnly cookie; no sessionStorage token is involved.
        assert a.get('/auth/password/me').status_code==200
        assert a.get('/notebooks').status_code==200
        # Unified logout revokes the server record, clears cookies and denies subsequent API access.
        out=a.post('/auth/logout',headers={'X-CSRF-Token':csrf}); assert out.status_code==200,out.text
        assert 'max-age=0' in out.headers.get('set-cookie','').lower()
        assert a.get('/notebooks').status_code==401
        assert a.post('/auth/password/login',json={"email":payload['email'],"password":payload['password']}).status_code==200
        assert a.get('/notebooks').status_code==200
        assert a.post('/auth/register',json=payload).status_code==409
    ''')


def test_expired_cookie_can_logout_and_clear_without_reviving_session():
    _run('''
        import time
        from fastapi.testclient import TestClient
        from server.main import app
        from core.lecture import store
        c=TestClient(app)
        p={"name":"Expiry","email":"expiry@example.com","password":"long-secure-password-123","confirmation":"long-secure-password-123"}
        assert c.post('/auth/register',json=p).status_code==200
        csrf=c.cookies.get('notiv_csrf'); sid=c.cookies.get('notiv_session')
        store.conn().execute('UPDATE auth_sessions SET expires_at=? WHERE id=?',(time.time()-1,__import__('hashlib').sha256(sid.encode()).hexdigest()));store.conn().commit()
        assert c.get('/notebooks').status_code==401
        r=c.post('/auth/logout',headers={'X-CSRF-Token':csrf}); assert r.status_code==200,r.text
        assert 'max-age=0' in r.headers.get('set-cookie','').lower()
        assert c.get('/notebooks').status_code==401
    ''')


def test_unconfigured_google_and_smtp_fail_honestly():
    _run('''
        import os
        from fastapi.testclient import TestClient
        from server.main import app
        for k in ('NOTIV_OIDC_ISSUER','NOTIV_OIDC_CLIENT_ID','NOTIV_OIDC_REDIRECT_URI','NOTIV_SMTP_HOST','NOTIV_SMTP_FROM'):
            os.environ.pop(k,None)
        c=TestClient(app,follow_redirects=False)
        cfg=c.get('/auth/config').json(); assert 'oidc' not in cfg and cfg['password'] is True
        assert c.get('/auth/login').status_code in (404, 405) and c.get('/auth/callback').status_code in (404, 405)
        p={"name":"Reset","email":"reset@example.com","password":"long-secure-password-123","confirmation":"long-secure-password-123"}
        assert c.post('/auth/register',json=p).status_code==200
        r=c.post('/auth/password/forgot',json={"email":p['email']}); assert r.status_code==503,r.text
        assert 'not configured' in r.text.lower() or 'delivery failed' in r.text.lower()
    ''')


def test_password_reset_is_one_time_and_revokes_existing_sessions():
    _run('''
        import server.api.routes_password_auth as password_auth
        from fastapi.testclient import TestClient
        from server.main import app
        c=TestClient(app)
        p={"name":"Reset Flow","email":"reset-flow@example.com","password":"long-secure-password-123","confirmation":"long-secure-password-123"}
        assert c.post('/auth/register',json=p).status_code==200
        captured={}
        def fake_send(email,token): captured['email']=email;captured['token']=token;return True
        password_auth._send_reset=fake_send
        r=c.post('/auth/password/forgot',json={"email":p['email']});assert r.status_code==200,r.text
        assert captured['email']==p['email'] and captured.get('token')
        new={"token":captured['token'],"password":"new-secure-password-456","confirmation":"new-secure-password-456"}
        done=c.post('/auth/password/reset',json=new);assert done.status_code==200,done.text
        assert c.get('/auth/password/me').status_code==401
        assert c.post('/auth/password/reset',json=new).status_code==400
        assert c.post('/auth/password/login',json={"email":p['email'],"password":p['password']}).status_code==401
        assert c.post('/auth/password/login',json={"email":p['email'],"password":new['password']}).status_code==200
    ''')
