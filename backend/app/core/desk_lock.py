"""Google sign-in for the trading desk.

Supabase Auth cannot check a password while its database refuses connections.
When GOOGLE_CLIENT_ID and GOOGLE_ALLOWED_EMAILS are set, the desk pages and
every trading API require a cookie from Sign in with Google. The ID token is
checked on the server against Google's published keys. Only the listed Gmail
addresses get a session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
from collections import defaultdict, deque

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "desk_session"
DEFAULT_SESSION_DAYS = 7


def _session_days() -> int:
    """How long a sign-in lasts: DESK_SESSION_DAYS (1-30), else 7 days."""
    try:
        days = int(os.environ.get("DESK_SESSION_DAYS", DEFAULT_SESSION_DAYS))
    except ValueError:
        days = DEFAULT_SESSION_DAYS
    return min(30, max(1, days))


TTL_SEC = _session_days() * 24 * 60 * 60
_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_JWKS_TTL_SEC = 60 * 60
_FAIL_LIMIT = 8
_FAIL_WINDOW_SEC = 10 * 60
_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_CLIENT_ID_RE = re.compile(r"[0-9A-Za-z._-]+")

_failures: dict[str, deque[float]] = defaultdict(deque)
_jwks_cache: tuple[float, dict] | None = None

router = APIRouter(tags=["session"])


def _client_id() -> str:
    raw = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not raw or _CLIENT_ID_RE.fullmatch(raw) is None:
        return ""
    return raw


def _allowed_emails() -> set[str]:
    raw = os.environ.get("GOOGLE_ALLOWED_EMAILS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _signing_key() -> bytes:
    return os.environ.get("ENCRYPTION_KEY", "").encode()


def desk_lock_enabled() -> bool:
    return bool(_client_id() and _allowed_emails() and _signing_key())


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _public_key(jwk: dict):
    e = int.from_bytes(_b64url(jwk["e"]), "big")
    n = int.from_bytes(_b64url(jwk["n"]), "big")
    return RSAPublicNumbers(e, n).public_key()


def verify_google_token(
    token: str,
    jwks: dict,
    *,
    client_id: str,
    allowed: set[str],
    now: float | None = None,
) -> tuple[str | None, str]:
    """Return (email, "") when the token is a real Google sign-in for an allowed user."""
    parts = token.split(".")
    if len(parts) != 3 or not client_id or not allowed:
        return None, "Sign in was rejected"
    try:
        header = json.loads(_b64url(parts[0]))
        if header.get("alg") != "RS256":
            return None, "Sign in was rejected"
        kid = header.get("kid")
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        jwk = next((item for item in keys or [] if item.get("kid") == kid), None)
        if not jwk:
            return None, "Sign in was rejected"
        signature = _b64url(parts[2])
        _public_key(jwk).verify(
            signature,
            f"{parts[0]}.{parts[1]}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(_b64url(parts[1]))
    except (InvalidSignature, ValueError, KeyError, json.JSONDecodeError):
        return None, "Sign in was rejected"

    current = now if now is not None else time.time()
    try:
        exp = float(claims.get("exp"))
    except (TypeError, ValueError):
        return None, "Sign in was rejected"
    if exp < current:
        return None, "Sign in expired. Try again."
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        return None, "Sign in was rejected"
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        return None, "Sign in was rejected"
    if claims.get("email_verified") is not True:
        return None, "That Google account has not verified its email."
    email = str(claims.get("email") or "").strip().lower()
    if email not in allowed:
        return None, "This Google account is not allowed to open the desk."
    return email, ""


async def fetch_jwks() -> dict:
    global _jwks_cache
    now = time.time()
    if _jwks_cache and _jwks_cache[0] > now:
        return _jwks_cache[1]
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.get(_JWKS_URL)
        res.raise_for_status()
        data = res.json()
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise ValueError("Google key set was not usable")
    _jwks_cache = (now + _JWKS_TTL_SEC, data)
    return data


def clear_jwks_cache() -> None:
    global _jwks_cache
    _jwks_cache = None


def _client_key(request: Request) -> str:
    fly_ip = (request.headers.get("fly-client-ip") or "").strip()
    if fly_ip:
        return fly_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _limited(key: str) -> bool:
    now = time.time()
    recent = _failures[key]
    while recent and now - recent[0] > _FAIL_WINDOW_SEC:
        recent.popleft()
    return len(recent) >= _FAIL_LIMIT


def _note_failure(key: str) -> None:
    _failures[key].append(time.time())


def clear_failures() -> None:
    _failures.clear()


def issue_token(now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + TTL_SEC)
    msg = str(exp).encode()
    sig = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(token: str | None, now: float | None = None) -> bool:
    key = _signing_key()
    if not token or not key or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit():
        return False
    if int(exp_s) < int(now if now is not None else time.time()):
        return False
    expected = hmac.new(key, exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def session_ok(conn) -> bool:
    if not desk_lock_enabled():
        return True
    return token_ok(conn.cookies.get(COOKIE))


def _api_public(path: str) -> bool:
    return path in ("/api/health", "/api/health/deep", "/api/session/google")


def _open_page(path: str) -> bool:
    if path in ("/login", "/login/", "/login.html"):
        return True
    if path.startswith("/_next/") or path.startswith("/api/") or path.startswith("/ws"):
        return True
    name = path.rsplit("/", 1)[-1]
    return "." in name and not name.endswith(".html")


async def desk_lock_middleware(request: Request, call_next):
    if not desk_lock_enabled() or request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if _api_public(path):
        return await call_next(request)
    signed_in = session_ok(request)
    if path.startswith("/api/") or path.startswith("/ws") or path.startswith("/sma"):
        if not signed_in:
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return await call_next(request)
    if path in ("/login", "/login/", "/login.html") and signed_in and request.method == "GET":
        return RedirectResponse("/trade/", status_code=302)
    if request.method == "GET" and not _open_page(path) and not signed_in:
        return RedirectResponse("/login/", status_code=302)
    return await call_next(request)


def _login_html(client_id: str) -> str:
    safe_id = html.escape(client_id, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ORB Desk</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0b1020; color: #e8eefc; font-family: sans-serif; }}
  main {{ width: min(420px, calc(100% - 32px)); }}
  h1 {{ font-size: 1.4rem; margin: 0 0 8px; }}
  p {{ color: #9fb0d0; line-height: 1.45; }}
  .err {{ color: #fb7185; min-height: 1.2em; }}
  #btn {{ margin-top: 16px; min-height: 44px; }}
</style>
</head>
<body>
<main>
  <h1>ORB Desk</h1>
  <p>Sign in with Google. Only the Google account allowed for this desk can open it or send an order.</p>
  <div class="err" id="e"></div>
  <div id="btn"></div>
</main>
<script src="https://accounts.google.com/gsi/client" async></script>
<script>
const clientId = "{safe_id}";
async function onSignIn(resp) {{
  const err = document.getElementById("e");
  err.textContent = "";
  let res;
  try {{
    res = await fetch("/api/session/google", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ credential: resp && resp.credential }}),
    }});
  }} catch (e) {{
    err.textContent = "Could not reach the desk.";
    return;
  }}
  if (!res.ok) {{
    let detail = "Sign in failed";
    try {{ detail = (await res.json()).detail || detail; }} catch (e) {{}}
    err.textContent = detail;
    return;
  }}
  location.replace("/trade/");
}}
function start() {{
  if (!window.google || !google.accounts || !google.accounts.id) {{
    setTimeout(start, 50);
    return;
  }}
  google.accounts.id.initialize({{ client_id: clientId, callback: onSignIn }});
  google.accounts.id.renderButton(document.getElementById("btn"), {{
    theme: "outline", size: "large", width: 320, text: "signin_with", shape: "pill"
  }});
}}
start();
</script>
</body>
</html>
"""


@router.post("/api/session/google")
async def google_login(request: Request):
    if not desk_lock_enabled():
        return JSONResponse({"detail": "Google sign-in is not turned on."}, status_code=404)
    key = _client_key(request)
    if _limited(key):
        return JSONResponse({"detail": "Too many attempts. Wait 10 minutes and try again."}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, str) or credential.count(".") != 2:
        _note_failure(key)
        return JSONResponse({"detail": "Sign in was rejected"}, status_code=401)
    try:
        jwks = await fetch_jwks()
    except (httpx.HTTPError, ValueError):
        return JSONResponse({"detail": "Google sign-in is unreachable. Try again in a minute."}, status_code=503)
    email, reason = verify_google_token(
        credential,
        jwks,
        client_id=_client_id(),
        allowed=_allowed_emails(),
    )
    if email is None:
        _note_failure(key)
        return JSONResponse({"detail": reason}, status_code=401)
    response = JSONResponse({"ok": True, "email": email})
    response.set_cookie(
        COOKIE,
        issue_token(),
        max_age=TTL_SEC,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/api/session/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/login")
@router.get("/login/")
async def login_page(request: Request):
    if desk_lock_enabled() and session_ok(request):
        return RedirectResponse("/trade/", status_code=302)
    client_id = _client_id()
    if not client_id:
        return HTMLResponse(
            "<!DOCTYPE html><html><body style=\"font-family:sans-serif;padding:24px\">"
            "<p>Google sign-in is not turned on yet.</p>"
            "<p><a href=\"/trade/\">Open the desk</a></p></body></html>"
        )
    return HTMLResponse(_login_html(client_id))


def install_desk_lock(app: FastAPI) -> None:
    app.middleware("http")(desk_lock_middleware)
    app.include_router(router)
