import pytest

from agentteam.app import create_app


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_health(path):
    client = create_app({"TESTING": True}).test_client()
    res = client.get(path)
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
