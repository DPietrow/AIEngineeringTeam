"""Hidden acceptance tests: PUT /notes/<id> with {"text": ...}."""

import pytest

from toyapp.app import create_app


def client():
    return create_app().test_client()


def test_update_changes_text_and_strips_whitespace():
    c = client()
    c.post("/notes", json={"text": "old"})
    res = c.put("/notes/1", json={"text": "  new text "})
    assert res.status_code == 200
    assert res.get_json() == {"id": 1, "text": "new text"}
    assert c.get("/notes").get_json() == [{"id": 1, "text": "new text"}]


def test_update_missing_note_is_404_with_error_body():
    res = client().put("/notes/9", json={"text": "x"})
    assert res.status_code == 404
    assert "error" in res.get_json()


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 5}])
def test_update_rejects_bad_input_with_400(body):
    c = client()
    c.post("/notes", json={"text": "keep"})
    res = c.put("/notes/1", json=body)
    assert res.status_code == 400
    assert "error" in res.get_json()
    assert c.get("/notes").get_json() == [{"id": 1, "text": "keep"}]
