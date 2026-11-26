import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from app.errors import ApiError
from app.services import assistants

CLIENT_ID = "g_test"
CLIENT_SECRET = "s3cret-client-value"
REDIRECT = "https://oauth-redirect.googleusercontent.com/r/home-test"


def seed_client(ctx, **overrides):
    kwargs = {
        "client_id": CLIENT_ID,
        "provider": "google",
        "name": "Google Home",
        "secret": CLIENT_SECRET,
        "redirect_uris": [REDIRECT],
    }
    kwargs.update(overrides)
    return asyncio.run(assistants.ensure_client(ctx, **kwargs))


def authorize(client, **params):
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "state": "xyz",
        "scope": "devices",
    }
    query.update(params)
    return client.get("/oauth/authorize", params=query)


def consent(client, code_params=None):
    return client.post(
        "/oauth/authorize/consent",
        json={
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT,
            "state": "xyz",
            "id_token": "test-token-a",
            **(code_params or {}),
        },
    )


def exchange(client, code, secret=CLIENT_SECRET, redirect=REDIRECT):
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": secret,
            "redirect_uri": redirect,
        },
    )


def test_authorize_page_renders_and_rejects_unknown_clients(client, ctx):
    page = authorize(client)
    assert page.status_code == 400  # client does not exist yet

    seed_client(ctx)
    ok = authorize(client)
    assert ok.status_code == 200
    assert "Google Home" in ok.text
    assert "oauth-redirect.googleusercontent.com" in ok.text

    assert authorize(client, client_id="nope").status_code == 400
    assert authorize(client, redirect_uri="https://evil.example/cb").status_code == 400
    assert authorize(client, response_type="token").status_code == 400


def test_google_style_long_state_survives_consent(client, ctx):
    """Google's state is an opaque blob well past 512 characters; it has to
    come back untouched on the redirect."""
    seed_client(ctx)
    state = "AMskz5oB" + "q" * 600
    assert authorize(client, state=state).status_code == 200

    response = consent(client, {"state": state})
    assert response.status_code == 200, response.text
    params = parse_qs(urlparse(response.json()["redirect_url"]).query)
    assert params["state"] == [state]


def test_authorization_code_flow_issues_and_rotates_tokens(client, ctx):
    seed_client(ctx)
    response = consent(client)
    assert response.status_code == 200, response.text
    redirect_url = response.json()["redirect_url"]
    parsed = urlparse(redirect_url)
    assert redirect_url.startswith(REDIRECT)
    assert parsed.query.startswith("code=")
    params = parse_qs(parsed.query)
    code = params["code"][0]
    assert params["state"] == ["xyz"]

    issued = exchange(client, code)
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].startswith("at_")
    assert body["refresh_token"].startswith("rt_")
    assert body["expires_in"] > 0

    # a code is single-use
    replay = exchange(client, code)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"

    link = asyncio.run(assistants.resolve_link(ctx, body["access_token"]))
    assert link["owner_id"] == "user-a"
    assert link["provider"] == "google"

    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body["refresh_token"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    new_access = refreshed.json()["access_token"]
    assert new_access != body["access_token"]
    asyncio.run(assistants.resolve_link(ctx, new_access))

    # the rotated access token is the only one that still works
    with pytest.raises(ApiError):
        asyncio.run(assistants.resolve_link(ctx, body["access_token"]))

    client.post("/oauth/revoke", data={"token": new_access})
    with pytest.raises(ApiError):
        asyncio.run(assistants.resolve_link(ctx, new_access))
    refresh_after_revoke = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body["refresh_token"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    assert refresh_after_revoke.status_code == 400


def test_token_endpoint_rejects_bad_secrets_and_redirect_mismatch(client, ctx):
    seed_client(ctx)
    code = parse_qs(urlparse(consent(client).json()["redirect_url"]).query)["code"][0]

    wrong_secret = exchange(client, code, secret="not-the-secret")
    assert wrong_secret.status_code == 401
    assert wrong_secret.json()["error"] == "invalid_client"

    seed_client(ctx, client_id="g_basic", redirect_uris=["https://other.example/cb"])
    other = consent(client, {"client_id": "g_basic"})
    assert other.status_code == 400  # redirect not registered for this client
    assert other.json()["error"] == "invalid_request"

    mismatch = exchange(client, code, redirect="https://elsewhere.example/cb")
    assert mismatch.status_code == 400
    assert mismatch.json()["error"] == "invalid_grant"

    unsupported = client.post(
        "/oauth/token",
        data={"grant_type": "password", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    assert unsupported.status_code == 400


def test_expired_access_token_is_rejected(client, ctx):
    client_doc = seed_client(ctx)
    issued = asyncio.run(assistants.issue_tokens(ctx, client_doc, "user-a"))
    asyncio.run(
        ctx.repo.update_link(
            issued["link"]["id"], {"access_expires_at": issued["link"]["created_at"]}
        )
    )
    with pytest.raises(ApiError) as err:
        asyncio.run(assistants.resolve_link(ctx, issued["access_token"]))
    assert err.value.code == "expired_token"


def test_links_api_lists_and_revokes(client, ctx):
    client_doc = seed_client(ctx)
    issued = asyncio.run(assistants.issue_tokens(ctx, client_doc, "user-a"))

    listed = client.get("/api/v1/assistants/links")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [issued["link"]["id"]]
    assert listed.json()[0]["provider"] == "google"
    assert listed.json()[0]["client_name"] == "Google Home"

    client.headers["Authorization"] = "Bearer test-token-b"
    assert client.delete(f"/api/v1/assistants/links/{issued['link']['id']}").status_code == 404

    client.headers["Authorization"] = "Bearer test-token-a"
    assert client.delete(f"/api/v1/assistants/links/{issued['link']['id']}").status_code == 204
    assert client.get("/api/v1/assistants/links").json() == []
    with pytest.raises(ApiError):
        asyncio.run(assistants.resolve_link(ctx, issued["access_token"]))
