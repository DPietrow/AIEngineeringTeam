"""Hidden acceptance tests: DELETE /notes/<id>. Agents never see this file."""

from toyapp.app import create_app


def client():
    return create_app().test_client()


def test_delete_existing_note_returns_204_and_removes_it():
    c = client()
    c.post("/notes", json={"text": "a"})
    c.post("/notes", json={"text": "b"})
    res = c.delete("/notes/1")
    assert res.status_code == 204
    assert res.data == b""
    assert c.get("/notes").get_json() == [{"id": 2, "text": "b"}]


def test_delete_missing_note_returns_404_with_error_body():
    c = client()
    res = c.delete("/notes/999")
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_delete_twice_second_is_404():
    c = client()
    c.post("/notes", json={"text": "a"})
    assert c.delete("/notes/1").status_code == 204
    assert c.delete("/notes/1").status_code == 404


def test_ids_are_not_reused_after_delete():
    c = client()
    c.post("/notes", json={"text": "a"})
    c.delete("/notes/1")
    assert c.post("/notes", json={"text": "b"}).get_json()["id"] == 2
