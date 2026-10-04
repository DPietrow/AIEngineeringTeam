"""Hidden acceptance tests: GET /notes/<id>."""

from toyapp.app import create_app


def client():
    return create_app().test_client()


def test_get_existing_note():
    c = client()
    c.post("/notes", json={"text": "hello"})
    c.post("/notes", json={"text": "world"})
    res = c.get("/notes/2")
    assert res.status_code == 200
    assert res.get_json() == {"id": 2, "text": "world"}


def test_get_missing_note_is_404_with_error_body():
    res = client().get("/notes/42")
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_listing_still_works():
    c = client()
    c.post("/notes", json={"text": "x"})
    assert c.get("/notes").get_json() == [{"id": 1, "text": "x"}]
