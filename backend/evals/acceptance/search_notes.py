"""Hidden acceptance tests: GET /notes?q=<text> filters by case-insensitive substring."""

from toyapp.app import create_app


def seeded():
    c = create_app().test_client()
    for text in ["Buy milk", "call MILKman", "walk dog"]:
        c.post("/notes", json={"text": text})
    return c


def test_filter_is_case_insensitive_substring_in_order():
    res = seeded().get("/notes?q=milk")
    assert res.status_code == 200
    assert [n["id"] for n in res.get_json()] == [1, 2]


def test_no_match_returns_empty_list():
    assert seeded().get("/notes?q=zebra").get_json() == []


def test_no_query_returns_everything():
    assert len(seeded().get("/notes").get_json()) == 3


def test_empty_query_returns_everything():
    assert len(seeded().get("/notes?q=").get_json()) == 3
