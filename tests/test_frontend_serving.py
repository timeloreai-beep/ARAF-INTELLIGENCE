from fastapi.testclient import TestClient

from backend.app.main import app


def test_fastapi_serves_frontend_without_exposing_env_file():
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.get("/.env").status_code == 404

