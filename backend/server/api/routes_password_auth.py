"""Local email/password authentication. Password reset email requires explicitly configured SMTP."""
import hashlib, hmac, os, secrets, smtplib, ssl, time
from email.message import EmailMessage
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from core.lecture import store, ratelimit
from core.lecture.errors import RateLimitError
from server.api.guards import client_ip
from server.auth_sessions import create, revoke, clear, digest
from server.auth_tokens import create_token
from core.lecture import settings

router = APIRouter(tags=["auth"])
NO_STORE = {"Cache-Control":"no-store"}

class RegisterBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str
    password: str = Field(min_length=12, max_length=128)
    confirmation: str = Field(min_length=12, max_length=128)
class LoginBody(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)
class ForgotBody(BaseModel): email: str
class ResetBody(BaseModel): token: str = Field(min_length=20, max_length=256); password: str = Field(min_length=12, max_length=128); confirmation: str = Field(min_length=12, max_length=128)

def _rate(request):
    try: ratelimit.check("auth_fail", client_ip(request.headers.get, request.client.host if request.client else None))
    except RateLimitError as e: raise HTTPException(429, "Too many attempts. Try again later.", headers={"Retry-After":str(e.retry_after)})

def _hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + value.hex()

def _verify(password: str, encoded: str) -> bool:
    try:
        alg,n,r,p,salt,val=encoded.split("$")
        got=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=int(n),r=int(r),p=int(p),dklen=len(bytes.fromhex(val)))
        return hmac.compare_digest(got.hex(),val)
    except Exception: return False

def _valid_email(email):
    import re
    return len(email) <= 254 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) is not None

def _user_by_email(email):
    return store.conn().execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()

def _start_session(uid, request):
    out=JSONResponse({"ok":True,"redirect":"/app"},headers=NO_STORE); create(uid,out,request); return out

@router.post("/auth/register")
def register(body: RegisterBody, request: Request):
    _rate(request)
    if not _valid_email(body.email): raise HTTPException(422,"Enter a valid email address.")
    if body.password != body.confirmation: raise HTTPException(422,"Passwords do not match.")
    if body.password.strip() != body.password or len(set(body.password)) < 4: raise HTTPException(422,"Choose a stronger password.")
    c=store.conn(); email=str(body.email).strip().lower()
    if _user_by_email(email): raise HTTPException(409,"An account with this email already exists. Try signing in.")
    now=time.time(); uid=int(c.execute("SELECT COALESCE(MAX(id),1000000000) AS m FROM users").fetchone()["m"])+1
    try:
        c.execute("INSERT INTO users(id,email,display_name,created_at,last_login_at,password_hash) VALUES(?,?,?,?,?,?)",(uid,email,body.name.strip(),now,now,_hash(body.password)))
        c.commit()
        store.ensure_personal_workspace(uid)
    except Exception:
        c.rollback(); raise HTTPException(500,"Account could not be created. Please try again.")
    return _start_session(uid,request)

@router.post("/auth/password/login")
def login(body: LoginBody, request: Request):
    _rate(request)
    if not _valid_email(body.email): raise HTTPException(422,"Email or password is incorrect.")
    user=_user_by_email(str(body.email).strip().lower())
    # Same-cost dummy hash for unknown users reduces obvious timing differences.
    valid=bool(user and user["password_hash"] and _verify(body.password,user["password_hash"]))
    if not valid: raise HTTPException(401,"Email or password is incorrect.")
    c=store.conn(); c.execute("UPDATE users SET last_login_at=? WHERE id=?",(time.time(),user["id"])); c.commit()
    return _start_session(int(user["id"]),request)

@router.post("/auth/password/logout")
def logout(request: Request):
    # Backwards-compatible alias for older clients; use the same CSRF, revocation and
    # expired-session cleanup semantics as the unified /auth/logout endpoint.
    from server import auth_sessions
    if not request.cookies.get(auth_sessions.COOKIE):
        raise HTTPException(401, "Session expired.")
    auth_sessions.validate_csrf(request, allow_expired=True)
    auth_sessions.revoke(request)
    out=JSONResponse({"ok":True},headers=NO_STORE)
    auth_sessions.clear(out, request)
    return out

@router.get("/auth/password/me")
def me(request: Request):
    from server.auth_sessions import resolve
    u=resolve(request)
    if not u: raise HTTPException(401,"Not signed in.")
    row=store.conn().execute("SELECT email,display_name FROM users WHERE id=?",(u["id"],)).fetchone()
    return JSONResponse({"id":u["id"],"email":row["email"],"name":row["display_name"]},headers=NO_STORE)

def _send_reset(email, token):
    host=settings.env("NOTIV_SMTP_HOST",""); sender=settings.env("NOTIV_SMTP_FROM","")
    if not host or not sender: return False
    port=settings.env_int("NOTIV_SMTP_PORT",587,lo=1,hi=65535); user=settings.env("NOTIV_SMTP_USER",""); password=settings.env("NOTIV_SMTP_PASSWORD","")
    base=settings.env("NOTIV_PUBLIC_BASE_URL","http://127.0.0.1:8000").rstrip("/")
    msg=EmailMessage(); msg["Subject"]="Reset your Notiv password"; msg["From"]=sender; msg["To"]=email
    msg.set_content("Use this one-time link to reset your Notiv password (expires in 30 minutes):\n\n"+base+"/reset-password?token="+token+"\n\nIf you did not request this, ignore this email.")
    with smtplib.SMTP(host,port,timeout=10) as server:
        server.starttls(context=ssl.create_default_context())
        if user: server.login(user,password)
        server.send_message(msg)
    return True

@router.post("/auth/password/forgot")
def forgot(body: ForgotBody, request: Request):
    _rate(request)
    if not _valid_email(body.email): raise HTTPException(422,"Enter a valid email address.")
    email=str(body.email).strip().lower(); user=_user_by_email(email)
    if user and user["password_hash"]:
        token=secrets.token_urlsafe(32); c=store.conn(); now=time.time()
        c.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",(now,user["id"]))
        c.execute("INSERT INTO password_resets(token_hash,user_id,expires_at,used_at,created_at) VALUES(?,?,?,?,?)",(digest(token),user["id"],now+1800,None,now)); c.commit()
        try: sent=_send_reset(email,token)
        except Exception: sent=False
        if not sent:
            c.execute("UPDATE password_resets SET used_at=? WHERE token_hash=?",(time.time(),digest(token))); c.commit()
            raise HTTPException(503,"Password reset email is not configured or delivery failed. Configure NOTIV_SMTP_* and try again.")
    return JSONResponse({"ok":True,"message":"If an account exists, password reset instructions will be sent."},headers=NO_STORE)

@router.post("/auth/password/reset")
def reset(body: ResetBody, request: Request):
    _rate(request)
    if body.password != body.confirmation: raise HTTPException(422,"Passwords do not match.")
    if body.password.strip()!=body.password or len(set(body.password))<4: raise HTTPException(422,"Choose a stronger password.")
    c=store.conn(); now=time.time(); row=c.execute("SELECT user_id FROM password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?",(digest(body.token),now)).fetchone()
    if not row: raise HTTPException(400,"This reset link is invalid, expired, or already used.")
    c.execute("UPDATE users SET password_hash=? WHERE id=?",(_hash(body.password),row["user_id"]))
    c.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",(now,row["user_id"]))
    c.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",(now,row["user_id"]))
    c.commit(); out=JSONResponse({"ok":True,"message":"Password updated. Sign in with your new password."},headers=NO_STORE); clear(out, request); return out
