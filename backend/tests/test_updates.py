def test_updates_are_empty_until_published(client):
    response = client.get("/api/v1/updates")
    assert response.status_code == 200
    assert response.json() == {"firmware": None, "app": None}
    assert response.headers["cache-control"] == "no-store"


def test_updates_forward_the_published_manifest(client, ctx):
    ctx.repo.release = {
        "firmware": {
            "version": "1.1.0",
            "sha256": "a" * 64,
            "url": "https://example.test/fanctl-1.1.0.bin",
        },
        "app": {
            "version": "1.1.0",
            "version_code": 2,
            "notes": "OTA updates",
            "url": "https://example.test/fanctl-1.1.0.apk",
        },
    }

    body = client.get("/api/v1/updates").json()
    assert body["firmware"]["version"] == "1.1.0"
    assert len(body["firmware"]["sha256"]) == 64
    assert body["app"]["version_code"] == 2
    assert body["app"]["notes"] == "OTA updates"


def test_updates_are_public(client):
    client.headers.pop("Authorization", None)
    response = client.get("/api/v1/updates")
    assert response.status_code == 200
