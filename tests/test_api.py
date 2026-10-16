import pytest
from fastapi.testclient import TestClient

from proxy_manager.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    app.state.manager.core.binary = None
    with TestClient(app) as client:
        yield client


def login(client):
    return client.post("/api/login", json={"password":"route-test-password-123"})


def test_state_and_mutations_require_authentication(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/subscriptions", json={"name":"测试", "url":"https://example.org/feed"}).status_code == 401
    assert client.get("/api/auth").json() == {"initialized":False, "authenticated":False}


def test_first_login_sets_password_cookie_and_logout_revokes_it(client):
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login", json={"password":"wrong-password"}).status_code == 401


def test_subscription_secret_is_not_in_events_or_public_files(client):
    login(client)
    secret_url = "https://example.org/feed?token=private-test-token"
    assert client.post("/api/subscriptions", json={"name":"我的订阅", "url":secret_url}).status_code == 200
    state = client.get("/api/state").json()
    assert state["subscriptions"][0]["url"] == secret_url
    assert "private-test-token" not in str(state["events"])
    assert "controller_secret" not in state
    assert client.get("/data/state.json").status_code == 404


def test_cross_origin_mutation_is_rejected(client):
    assert client.post("/api/login", headers={"Origin":"https://other.example"}, json={"password":"route-test-password-123"}).status_code == 403


def test_input_validation_rejects_invalid_links_and_open_remote_proxy(client):
    login(client)
    assert client.post("/api/subscriptions", json={"name":"测试", "url":"file:///C:/secret"}).status_code == 422
    settings = client.get("/api/state").json()["settings"]
    assert client.put("/api/settings", json=settings | {"allow_lan":True}).status_code == 422
    assert client.put("/api/settings", json=settings | {"excluded_regions":["INVALID"]}).status_code == 422
    assert client.put("/api/settings", json=settings | {"allowed_ips":["not-an-ip"]}).status_code == 422


def test_login_rate_limit(client):
    login(client)
    client.post("/api/logout")
    for _ in range(5):
        assert client.post("/api/login", json={"password":"wrong-password"}).status_code == 401
    assert login(client).status_code == 429


def test_primary_routing_requires_login_and_valid_enabled_subscription(client):
    assert client.put("/api/routing",json={"strategy":"primary","primary_subscription_id":"unknown"}).status_code == 401
    login(client)
    assert client.post("/api/subscriptions",json={"name":"主线路","url":"https://example.org/main"}).status_code == 200
    assert client.post("/api/subscriptions",json={"name":"备用","url":"https://example.org/backup","enabled":False}).status_code == 200
    state = client.get("/api/state").json()
    primary, disabled = [s["id"] for s in state["subscriptions"]]
    assert client.put("/api/routing",json={"strategy":"primary","primary_subscription_id":"unknown"}).status_code == 400
    assert client.put("/api/routing",json={"strategy":"primary","primary_subscription_id":disabled}).status_code == 400
    assert client.put("/api/routing",json={"strategy":"invalid"}).status_code == 422

    async def validated(state):
        pass
    client.app.state.manager.core.apply = validated
    assert client.put("/api/routing",json={"strategy":"primary","primary_subscription_id":primary}).status_code == 200
    state = client.get("/api/state").json()
    assert state["settings"]["selection_strategy"] == "primary"
    assert state["routing"]["primary_name"] == "主线路"
    assert client.put("/api/routing",json={"strategy":"latency"}).status_code == 200
    assert client.get("/api/state").json()["settings"]["primary_subscription_id"] is None
