from fastapi.testclient import TestClient

from backend.app.main import app


def test_fastapi_serves_frontend_without_exposing_env_file():
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/engines.js").status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.get("/admin-ghost-control").status_code == 200
    ghost_page = client.get("/admin/ghost/page")
    assert ghost_page.status_code == 200
    assert "User Management" in ghost_page.text
    assert client.get("/ghost-admin.js").status_code == 200
    assert client.get("/.env").status_code == 404


def test_fastapi_serves_terminal_files_from_static_directory():
    client = TestClient(app)

    assert "ARAF INTELLIGENCE" in client.get("/").text
    assert "HARDENED INSTITUTIONAL ENGINE" in client.get("/app.js").text
    assert "araf-api-base" in client.get("/").text

