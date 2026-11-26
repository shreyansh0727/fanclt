"""OAuth 2.0 authorization server used by Google Home and SmartThings.

Both platforms implement the authorization-code grant with a confidential
client: the Home app / SmartThings app opens /oauth/authorize in a browser, the
owner signs in with their FanCtl (Firebase) account, and the browser comes back
to the platform's redirect URI with a one-time code. Only token hashes are kept.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..errors import ApiError
from ..models import OAuthConsentRequest
from ..services import assistants
from ..timeutil import aware, utcnow
from .deps import get_ctx

log = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

_CONSENT_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link FanCtl</title>
<style>
:root{color-scheme:dark}
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#06080d;color:#e6e9ef;display:flex;min-height:100vh;align-items:center;justify-content:center}
main{width:100%;max-width:420px;padding:32px 24px;background:#111318;border:1px solid #232833;border-radius:28px;margin:24px}
h1{font-size:22px;margin:0 0 6px}
p{color:#9aa4b2;font-size:14px;line-height:1.5}
.who{background:#1a1f27;border-radius:14px;padding:12px 14px;font-size:14px;margin:18px 0}
button{width:100%;padding:14px;border:0;border-radius:999px;background:#a9c7ff;color:#0b1220;font-size:16px;font-weight:600;cursor:pointer}
button.tonal{background:#2b3240;color:#e6e9ef;margin-top:10px}
form{margin:18px 0 0}
label{display:block;font-size:12px;color:#9aa4b2;margin:10px 0 6px}
input{width:100%;box-sizing:border-box;padding:12px;border-radius:12px;border:1px solid #2b3240;background:#0e1218;color:#e6e9ef;font-size:15px}
summary{cursor:pointer;color:#9aa4b2;font-size:13px;margin-top:14px}
#msg{margin-top:14px;font-size:14px;color:#ffb4ab;min-height:20px}
.ok{color:#8fd18f}
</style></head><body><main>
<h1>Link FanCtl</h1>
<p><strong id="client"></strong> wants to control the controllers in your FanCtl account.</p>
<div class="who">Sign in with the same account you use in the FanCtl app.</div>
<button id="google">Continue with Google</button>
<details><summary>Use email and password</summary>
<form id="email-form">
<label for="email">Email</label><input id="email" type="email" autocomplete="username" required>
<label for="pass">Password</label><input id="pass" type="password" autocomplete="current-password" required>
<button class="tonal" type="submit">Sign in</button>
</form></details>
<div id="msg"></div>
<script>
const CONSENT = __CONFIG__;
document.getElementById('client').textContent = CONSENT.client_name;

let auth = null;
let signOutHint = '';

function fail(text) { document.getElementById('msg').textContent = text; }

async function finish(user) {
  try {
    fail('Linking...');
    const idToken = await user.getIdToken(true);
    const response = await fetch('/oauth/authorize/consent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...CONSENT.form, id_token: idToken }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.redirect_url) {
      window.location.href = data.redirect_url;
      return;
    }
    fail((data.error && (data.error.message || data.error)) || 'Could not link this account.');
    if (auth) { await auth.signOut().catch(() => {}); }
  } catch (error) {
    fail(String((error && error.message) || error));
  }
}

function boot() {
  if (!CONSENT.firebase.apiKey) {
    document.getElementById('google').disabled = true;
    fail('Voice linking is not configured on this server.');
    return;
  }
  firebase.initializeApp(CONSENT.firebase);
  auth = firebase.auth();
  document.getElementById('google').addEventListener('click', async () => {
    try {
      const result = await auth.signInWithPopup(new firebase.auth.GoogleAuthProvider());
      await finish(result.user);
    } catch (error) {
      fail(String((error && error.message) || error));
    }
  });
  document.getElementById('email-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const result = await auth.signInWithEmailAndPassword(
        document.getElementById('email').value,
        document.getElementById('pass').value,
      );
      await finish(result.user);
    } catch (error) {
      fail(String((error && error.message) || error));
    }
  });
}
</script>
<script src="https://www.gstatic.com/firebasejs/10.12.5/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.5/firebase-auth-compat.js"></script>
<script>boot();</script>
</body></html>"""

_AUTHORIZE_ERROR = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Link FanCtl</title>
<style>body{font-family:system-ui,sans-serif;background:#06080d;color:#e6e9ef;padding:40px}</style>
</head><body><h1>Link FanCtl</h1><p>__MESSAGE__</p></body></html>"""


def _oauth_error(code: str, message: str, status: int = 400) -> JSONResponse:
    response = JSONResponse({"error": code, "error_description": message}, status_code=status)
    response.headers["Cache-Control"] = "no-store"
    return response


def _redirect_allowed(client: dict, redirect_uri: str) -> bool:
    return bool(redirect_uri) and redirect_uri in (client.get("redirect_uris") or [])


def _render_consent(ctx, client: dict, redirect_uri: str, state: str | None, scope: str | None) -> str:
    settings = ctx.settings
    config = {
        "client_name": client.get("name") or client["id"],
        "form": {
            "client_id": client["id"],
            "redirect_uri": redirect_uri,
            "state": state or "",
            "scope": scope or "",
        },
        "firebase": {
            "apiKey": settings.firebase_api_key or "",
            "authDomain": f"{settings.project_id or 'fanctl'}.firebaseapp.com",
            "projectId": settings.project_id or "",
        },
    }
    # </script> inside a JSON string would close the block early.
    blob = json.dumps(config).replace("</", "<\\/")
    return _CONSENT_PAGE.replace("__CONFIG__", blob)


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize(
    request: Request,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    scope: str = "",
    ctx=Depends(get_ctx),
):
    client = await ctx.repo.get_oauth_client(client_id) if client_id else None
    if not client or not _redirect_allowed(client, redirect_uri):
        message = "This link is not valid: the client or redirect URI is not registered."
        return HTMLResponse(_AUTHORIZE_ERROR.replace("__MESSAGE__", message), status_code=400)
    if response_type != "code":
        return HTMLResponse(
            _AUTHORIZE_ERROR.replace("__MESSAGE__", "Only response_type=code is supported."),
            status_code=400,
        )
    return HTMLResponse(_render_consent(ctx, client, redirect_uri, state, scope))


@router.post("/oauth/authorize/consent")
async def consent(req: OAuthConsentRequest, ctx=Depends(get_ctx)):
    try:
        client = await assistants.verify_client(ctx, req.client_id)
    except ApiError as exc:
        return _oauth_error(exc.code, exc.message, exc.status_code)
    if not _redirect_allowed(client, req.redirect_uri):
        return _oauth_error("invalid_request", "redirect_uri is not registered for this client")

    user = ctx.verify_token(req.id_token)
    code = assistants.new_token("code_")
    now = utcnow()
    await ctx.repo.create_auth_code(
        {
            "id": assistants.hash_token(code),
            "owner_id": user.uid,
            "client_id": client["id"],
            "redirect_uri": req.redirect_uri,
            "scopes": (req.scope or "").split(),
            "created_at": now,
            "expires_at": now + timedelta(seconds=ctx.settings.oauth_code_ttl_s),
            "used_at": None,
        }
    )
    params = {"code": code}
    if req.state:
        params["state"] = req.state
    separator = "&" if "?" in req.redirect_uri else "?"
    log.info("assistant %s linked for %s", client.get("provider") or client["id"], user.uid)
    return {"redirect_url": f"{req.redirect_uri}{separator}{urlencode(params)}"}


def _client_credentials(request: Request, form) -> tuple[str, str | None]:
    client_id = str(form.get("client_id") or "")
    secret = form.get("client_secret")
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(header[6:]).decode()
            basic_id, _, basic_secret = raw.partition(":")
            client_id = client_id or basic_id
            if secret is None:
                secret = basic_secret
        except Exception:
            pass
    return client_id, None if secret is None else str(secret)


@router.post("/oauth/token")
async def token(request: Request, ctx=Depends(get_ctx)):
    form = await request.form()
    grant_type = str(form.get("grant_type") or "")
    client_id, client_secret = _client_credentials(request, form)
    try:
        client = await assistants.verify_client(ctx, client_id, client_secret, require_secret=True)
    except ApiError as exc:
        return _oauth_error(exc.code, exc.message, exc.status_code)

    if grant_type == "authorization_code":
        raw_code = str(form.get("code") or "")
        code_hash = assistants.hash_token(raw_code)
        stored = await ctx.repo.get_auth_code(code_hash) if raw_code else None
        if not stored or stored.get("client_id") != client["id"] or stored.get("used_at"):
            return _oauth_error("invalid_grant", "authorization code is not valid")
        expires = aware(stored.get("expires_at"))
        if expires and expires <= utcnow():
            return _oauth_error("invalid_grant", "authorization code expired")
        if str(form.get("redirect_uri") or "") != stored.get("redirect_uri"):
            return _oauth_error("invalid_grant", "redirect_uri does not match the code")
        await ctx.repo.update_auth_code(code_hash, {"used_at": utcnow()})
        issued = await assistants.issue_tokens(ctx, client, stored["owner_id"])
        response = JSONResponse(
            {
                "access_token": issued["access_token"],
                "token_type": "Bearer",
                "expires_in": issued["expires_in"],
                "refresh_token": issued["refresh_token"],
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    if grant_type == "refresh_token":
        raw_refresh = str(form.get("refresh_token") or "")
        try:
            body = await assistants.refresh_access_token(ctx, client, raw_refresh)
        except ApiError as exc:
            return _oauth_error(exc.code, exc.message, exc.status_code)
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        return response

    return _oauth_error("unsupported_grant_type", "only authorization_code and refresh_token are supported")


@router.post("/oauth/revoke")
async def revoke(request: Request, ctx=Depends(get_ctx)):
    form = await request.form()
    raw = str(form.get("token") or "")
    if raw:
        for field in ("access_hash", "refresh_hash"):
            link = await ctx.repo.get_link_by_hash(field, assistants.hash_token(raw))
            if link:
                await assistants.revoke_link(ctx, link)
                break
    return JSONResponse({})
