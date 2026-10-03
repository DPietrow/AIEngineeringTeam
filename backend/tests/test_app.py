from agentteam.app import create_app


def test_health():
    client = create_app({"TESTING": True}).test_client()
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
