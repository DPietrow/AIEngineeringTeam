import pytest

from agentteam.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "app.db")})
    return app.test_client()


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_health(client, path):
    res = client.get(path)
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
