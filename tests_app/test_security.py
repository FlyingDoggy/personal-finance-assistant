from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.security import install_security


def client():
    app = FastAPI()
    install_security(app, token="synthetic-secret")
    @app.get("/api/v1/private")
    def private():
        return {"secret": "synthetic"}
    return TestClient(app, base_url="http://127.0.0.1")


def test_session_cannot_be_bootstrapped_cross_site():
    c = client()
    assert c.get("/api/v1/private").status_code == 401
    for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"},
                    {"host": "evil.example"}, {"origin": "http://127.0.0.1:9999"}):
        assert c.get("/api/v1/session", headers=headers).status_code == 403
    response = c.get("/api/v1/session")
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert c.get("/api/v1/private").status_code == 200
    assert c.get("/api/v1/private", headers={"origin": "null"}).status_code == 403


def test_query_or_bearer_cannot_impersonate_browser_session():
    c = client()
    response = c.get("/api/v1/private?token=synthetic-secret",
                     headers={"authorization": "Bearer synthetic-secret"})
    assert response.status_code == 401
