"""Bearer-token verification (HS256 JWT) — stdlib only, no web framework.

This is real verification, not a bypass: signature (constant-time), exact
algorithm (`none` and anything but HS256 rejected), required `exp` and `sub`.
It is the smallest proper default for `get_current_user`. If Notiv already has
an identity provider / session store, replace the body of
`server.api.deps.get_current_user`; the contract the lecture routes rely on is
only that it returns {"id": <int user id>}.
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Optional, Sequence

MIN_SECRET_BYTES = 32


class AuthError(Exception):
    pass


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(secret: str, signing_input: str) -> bytes:
    return hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()


MAX_TOKEN_BYTES = 4096
MAX_USER_ID = 2 ** 63 - 1


def create_token(user_id: int, secret: str, ttl_seconds: int = 3600, now: Optional[float] = None,
                 issuer: Optional[str] = None, audience: Optional[str] = None) -> str:
    """Issuer side (login service / tests)."""
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise AuthError(f"secret must be at least {MIN_SECRET_BYTES} bytes")
    t = int(now if now is not None else time.time())
    h = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"sub": str(int(user_id)), "iat": t, "exp": t + int(ttl_seconds)}
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    p = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    return f"{h}.{p}.{_b64e(_sign(secret, f'{h}.{p}'))}"


def verify_token(token: str, secret: str, now: Optional[float] = None, *, previous_secrets: Sequence[str] = (),
                 issuer: Optional[str] = None, audience: Optional[str] = None, max_ttl: Optional[int] = None,
                 leeway: int = 0) -> int:
    """Returns the user id, or raises AuthError (never returns a default user).

    Beyond signature/alg/exp/sub: `previous_secrets` accept tokens signed with a ROTATED-OUT secret (rotate without
    logging everyone out); `nbf` honoured; `issuer`/`audience` pinned when configured; `max_ttl` rejects tokens whose
    lifetime (exp - iat) is longer than allowed or that lack iat; oversize tokens and out-of-range user ids rejected.
    `leeway` (clock skew, seconds) defaults to 0 so expiry is exact.
    """
    if not secret or len(secret.encode()) < MIN_SECRET_BYTES:
        raise AuthError("authentication is not configured")
    if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES:
        raise AuthError("malformed token")
    try:
        h, p, s = token.split(".")
        header, payload = json.loads(_b64d(h)), json.loads(_b64d(p))
        sig = _b64d(s)
    except Exception:
        raise AuthError("malformed token")
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise AuthError("malformed token")
    if header.get("alg") != "HS256":
        raise AuthError("unsupported algorithm")
    ok = False
    for sec in (secret, *[x for x in previous_secrets if x and len(x.encode()) >= MIN_SECRET_BYTES]):
        ok |= hmac.compare_digest(sig, _sign(sec, f"{h}.{p}"))        # no early exit: constant-ish time
    if not ok:
        raise AuthError("bad signature")
    t = now if now is not None else time.time()
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or t >= exp + leeway:
        raise AuthError("token expired")
    nbf = payload.get("nbf")
    if nbf is not None and (not isinstance(nbf, (int, float)) or t + leeway < nbf):
        raise AuthError("token not yet valid")
    if max_ttl is not None:
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)) or exp - iat > max_ttl:
            raise AuthError("token lifetime too long")
    if issuer is not None and payload.get("iss") != issuer:
        raise AuthError("wrong issuer")
    if audience is not None:
        aud = payload.get("aud")
        if audience != aud and not (isinstance(aud, list) and audience in aud):
            raise AuthError("wrong audience")
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise AuthError("token has no valid subject")
    if not 1 <= uid <= MAX_USER_ID:
        raise AuthError("token has no valid subject")
    return uid


def verify_from_env(token: str, now: Optional[float] = None) -> int:
    """verify_token configured from NOTIV_AUTH_* (secret may come from NOTIV_AUTH_SECRET_FILE)."""
    from core.lecture import settings
    prev = [x.strip() for x in settings.env("NOTIV_AUTH_SECRET_PREVIOUS", "").split(",") if x.strip()]
    mt = settings.env("NOTIV_AUTH_MAX_TTL_SECONDS", "")
    return verify_token(token, settings.env("NOTIV_AUTH_SECRET", ""), now, previous_secrets=prev,
                        issuer=settings.env("NOTIV_AUTH_ISSUER", "") or None,
                        audience=settings.env("NOTIV_AUTH_AUDIENCE", "") or None,
                        max_ttl=int(mt) if mt.isdigit() else None)
