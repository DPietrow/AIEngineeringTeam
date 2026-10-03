"""Hidden acceptance tests: GET /notes/count returns {"count": <n>}."""

from toyapp.app import create_app


def test_count_starts_at_zero():
    res = create_app().test_client().get("/notes/count")
    assert res.status_code == 200
    assert res.get_json() == {"count": 0}


def test_count_tracks_created_notes():
    c = create_app().test_client()
    c.post("/notes", json={"text": "a"})
    c.post("/notes", json={"text": "b"})
    assert c.get("/notes/count").get_json() == {"count": 2}


def test_notes_listing_unchanged():
    c = create_app().test_client()
    c.post("/notes", json={"text": "a"})
    assert c.get("/notes").get_json() == [{"id": 1, "text": "a"}]
