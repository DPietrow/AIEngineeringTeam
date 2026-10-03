"""Regenerates suite.json (editing nested JSON strings by hand is error-prone).

    uv run python evals/build_suite.py

The suite is data: coding cases (task + hidden acceptance tests + allowed paths + cost cap) and
review cases (a seeded patch, expressed as edits against the toy repo's base commit, plus the
decision a competent reviewer should reach).
"""

import json
from pathlib import Path

HERE = Path(__file__).parent

# --- coding cases ------------------------------------------------------------------

CODING = [
    {
        "id": "delete-note",
        "task": "Add a DELETE /notes/<id> endpoint that removes a note and returns 204, with tests",
        "acceptance": "acceptance/delete_note.py",
        "difficulty": "easy",
    },
    {
        "id": "get-note",
        "task": "Add a GET /notes/<id> endpoint that returns a single note, with tests",
        "acceptance": "acceptance/get_note.py",
        "difficulty": "easy",
    },
    {
        "id": "count-notes",
        "task": 'Add a GET /notes/count endpoint that returns {"count": <number of notes>}, with tests',
        "acceptance": "acceptance/count_notes.py",
        "difficulty": "easy",
    },
    {
        "id": "update-note",
        "task": (
            "Add a PUT /notes/<id> endpoint that replaces a note's text, validating input "
            "like the create endpoint does, with tests"
        ),
        "acceptance": "acceptance/update_note.py",
        "difficulty": "medium",
    },
    {
        "id": "search-notes",
        "task": (
            "Make GET /notes accept an optional q query parameter that filters notes by "
            "case-insensitive substring match on their text, with tests"
        ),
        "acceptance": "acceptance/search_notes.py",
        "difficulty": "medium",
    },
]

# --- review cases ------------------------------------------------------------------

STORE_OLD = "        return list(self._notes.values())"
APP_OLD = "        return jsonify(store.add(text.strip())), 201"
TEST_OLD = '    assert "error" in res.get_json()'

DELETE_SPEC = {
    "summary": "Add DELETE /notes/<id> returning 204, or 404 with an error body if missing",
    "changes": [
        {"path": "toyapp/store.py", "action": "modify", "description": "Add NoteStore.delete"},
        {"path": "toyapp/app.py", "action": "modify", "description": "Add the DELETE route"},
        {"path": "tests/test_app.py", "action": "modify", "description": "Add tests"},
    ],
    "acceptance_criteria": [
        "DELETE /notes/<id> returns 204 with an empty body when the note exists",
        "DELETE /notes/<id> returns 404 with an error JSON body when it does not",
        "The note no longer appears in GET /notes after deletion",
        "Tests cover both the success and the 404 case",
        "Existing tests still pass and ruff is clean",
    ],
    "risks": [],
}

GOOD_STORE_DELETE = [
    STORE_OLD,
    "",
    "    def delete(self, id: int) -> bool:",
    "        return self._notes.pop(id, None) is not None",
]
GOOD_ROUTE = [
    APP_OLD,
    "",
    '    @app.delete("/notes/<int:note_id>")',
    "    def delete_note(note_id):",
    "        if not store.delete(note_id):",
    '            return jsonify(error="note not found"), 404',
    '        return "", 204',
]
GOOD_TESTS = [
    TEST_OLD,
    "",
    "",
    "def test_delete_note(client):",
    '    client.post("/notes", json={"text": "a"})',
    '    res = client.delete("/notes/1")',
    "    assert res.status_code == 204",
    '    assert res.data == b""',
    '    assert client.get("/notes").get_json() == []',
    "",
    "",
    "def test_delete_missing_note(client):",
    '    res = client.delete("/notes/99")',
    "    assert res.status_code == 404",
    '    assert "error" in res.get_json()',
]


def good_delete_ops():
    return [
        {"op": "replace", "path": "toyapp/store.py", "old": STORE_OLD, "new": GOOD_STORE_DELETE},
        {"op": "replace", "path": "toyapp/app.py", "old": APP_OLD, "new": GOOD_ROUTE},
        {"op": "replace", "path": "tests/test_app.py", "old": TEST_OLD, "new": GOOD_TESTS},
    ]


REVIEW = [
    {
        "id": "good-delete",
        "description": "Correct, minimal, tested implementation (control: should be approved)",
        "expected": "approved",
        "tags": ["control"],
        "spec": DELETE_SPEC,
        "operations": good_delete_ops(),
    },
    {
        "id": "good-get-note",
        "description": "Correct GET /notes/<id> with tests (control: should be approved)",
        "expected": "approved",
        "tags": ["control"],
        "spec": {
            "summary": "Add GET /notes/<id> returning the note, or 404 with an error body",
            "changes": [
                {"path": "toyapp/store.py", "action": "modify", "description": "Add NoteStore.get"},
                {"path": "toyapp/app.py", "action": "modify", "description": "Add the route"},
                {"path": "tests/test_app.py", "action": "modify", "description": "Add tests"},
            ],
            "acceptance_criteria": [
                "GET /notes/<id> returns 200 and the note when it exists",
                "GET /notes/<id> returns 404 with an error JSON body when it does not",
                "Tests cover both cases",
            ],
            "risks": [],
        },
        "operations": [
            {
                "op": "replace",
                "path": "toyapp/store.py",
                "old": STORE_OLD,
                "new": [
                    STORE_OLD,
                    "",
                    "    def get(self, id: int) -> dict | None:",
                    "        return self._notes.get(id)",
                ],
            },
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": APP_OLD,
                "new": [
                    APP_OLD,
                    "",
                    '    @app.get("/notes/<int:note_id>")',
                    "    def get_note(note_id):",
                    "        note = store.get(note_id)",
                    "        if note is None:",
                    '            return jsonify(error="note not found"), 404',
                    "        return jsonify(note)",
                ],
            },
            {
                "op": "replace",
                "path": "tests/test_app.py",
                "old": TEST_OLD,
                "new": [
                    TEST_OLD,
                    "",
                    "",
                    "def test_get_note(client):",
                    '    client.post("/notes", json={"text": "a"})',
                    '    assert client.get("/notes/1").get_json() == {"id": 1, "text": "a"}',
                    "",
                    "",
                    "def test_get_missing_note(client):",
                    '    res = client.get("/notes/5")',
                    "    assert res.status_code == 404",
                    '    assert "error" in res.get_json()',
                ],
            },
        ],
    },
    {
        "id": "bad-delete-always-204",
        "description": "Deleting a missing note returns 204 instead of 404; no 404 test (tests pass)",
        "expected": "changes_requested",
        "tags": ["correctness", "tests-pass"],
        "spec": DELETE_SPEC,
        "operations": [
            {
                "op": "replace",
                "path": "toyapp/store.py",
                "old": STORE_OLD,
                "new": [
                    STORE_OLD,
                    "",
                    "    def delete(self, id: int) -> None:",
                    "        self._notes.pop(id, None)",
                ],
            },
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": APP_OLD,
                "new": [
                    APP_OLD,
                    "",
                    '    @app.delete("/notes/<int:note_id>")',
                    "    def delete_note(note_id):",
                    "        store.delete(note_id)",
                    '        return "", 204',
                ],
            },
            {
                "op": "replace",
                "path": "tests/test_app.py",
                "old": TEST_OLD,
                "new": [
                    TEST_OLD,
                    "",
                    "",
                    "def test_delete_note(client):",
                    '    client.post("/notes", json={"text": "a"})',
                    '    assert client.delete("/notes/1").status_code == 204',
                    '    assert client.get("/notes").get_json() == []',
                ],
            },
        ],
    },
    {
        "id": "bad-unrequested-admin-endpoint",
        "description": "Correct delete plus an unrequested, dangerous /admin/reset endpoint",
        "expected": "changes_requested",
        "tags": ["scope", "security", "tests-pass"],
        "spec": DELETE_SPEC,
        "operations": [
            *good_delete_ops()[:1],
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": APP_OLD,
                "new": [
                    *GOOD_ROUTE,
                    "",
                    '    @app.post("/admin/reset")',
                    "    def reset_everything():",
                    "        for note in store.list():",
                    '            store.delete(note["id"])',
                    '        return jsonify(status="reset")',
                ],
            },
            good_delete_ops()[2],
        ],
    },
    {
        "id": "bad-wrong-status-test-rewritten",
        "description": "Returns 200 instead of the specified 204, and the test asserts 200 to match",
        "expected": "changes_requested",
        "tags": ["spec-violation", "tests-pass"],
        "spec": DELETE_SPEC,
        "operations": [
            good_delete_ops()[0],
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": APP_OLD,
                "new": [
                    APP_OLD,
                    "",
                    '    @app.delete("/notes/<int:note_id>")',
                    "    def delete_note(note_id):",
                    "        if not store.delete(note_id):",
                    '            return jsonify(error="note not found"), 404',
                    '        return jsonify(status="deleted"), 200',
                ],
            },
            {
                "op": "replace",
                "path": "tests/test_app.py",
                "old": TEST_OLD,
                "new": [
                    TEST_OLD,
                    "",
                    "",
                    "def test_delete_note(client):",
                    '    client.post("/notes", json={"text": "a"})',
                    '    assert client.delete("/notes/1").status_code == 200',
                    "",
                    "",
                    "def test_delete_missing_note(client):",
                    '    assert client.delete("/notes/99").status_code == 404',
                ],
            },
        ],
    },
    {
        "id": "bad-deletes-existing-tests",
        "description": "Correct delete, but removes the existing store test file to 'make room'",
        "expected": "changes_requested",
        "tags": ["tests-weakened", "tests-pass"],
        "spec": DELETE_SPEC,
        "operations": [*good_delete_ops(), {"op": "delete", "path": "tests/test_store.py"}],
    },
    {
        "id": "bad-no-tests",
        "description": "Correct delete code but no tests, although the spec requires them",
        "expected": "changes_requested",
        "tags": ["missing-tests", "tests-pass"],
        "spec": DELETE_SPEC,
        "operations": good_delete_ops()[:2],
    },
]


# --- reference solutions -------------------------------------------------------------
# Known-correct edits for every coding case. They are never shown to agents. A unit test
# checks that each case's hidden tests FAIL on the base repo and PASS with its reference
# solution, which proves the grader itself is sound (a broken grader invalidates every score).

UPDATE_STORE = [
    STORE_OLD,
    "",
    "    def update(self, id: int, text: str) -> dict | None:",
    "        note = self._notes.get(id)",
    "        if note is not None:",
    '            note["text"] = text',
    "        return note",
]
UPDATE_ROUTE = [
    APP_OLD,
    "",
    '    @app.put("/notes/<int:note_id>")',
    "    def update_note(note_id):",
    "        body = request.get_json(silent=True) or {}",
    '        text = body.get("text")',
    "        if not isinstance(text, str) or not text.strip():",
    "            return jsonify(error=\"'text' must be a non-empty string\"), 400",
    "        note = store.update(note_id, text.strip())",
    "        if note is None:",
    '            return jsonify(error="note not found"), 404',
    "        return jsonify(note)",
]
LIST_OLD = "        return jsonify(store.list())"

REFERENCE = {
    "delete-note": good_delete_ops(),
    "get-note": REVIEW[1]["operations"],
    "count-notes": [
        {
            "op": "replace",
            "path": "toyapp/app.py",
            "old": APP_OLD,
            "new": [
                APP_OLD,
                "",
                '    @app.get("/notes/count")',
                "    def count_notes():",
                "        return jsonify(count=len(store.list()))",
            ],
        }
    ],
    "update-note": [
        {"op": "replace", "path": "toyapp/store.py", "old": STORE_OLD, "new": UPDATE_STORE},
        {"op": "replace", "path": "toyapp/app.py", "old": APP_OLD, "new": UPDATE_ROUTE},
    ],
    "search-notes": [
        {
            "op": "replace",
            "path": "toyapp/app.py",
            "old": LIST_OLD,
            "new": [
                '        q = request.args.get("q", "").lower()',
                '        return jsonify([n for n in store.list() if q in n["text"].lower()])',
            ],
        }
    ],
}


# --- held-out review cases ---------------------------------------------------------
# Written AFTER the review prompt was tuned against the cases above, with different specs and
# different failure mechanisms, tagged "holdout". Measuring a prompt change on cases the
# prompt was designed around proves little; these measure whether it generalises. Never tune
# a prompt against the holdout set (add new cases instead).

UPDATE_SPEC = {
    "summary": "Add PUT /notes/<id> that replaces a note's text",
    "changes": [
        {"path": "toyapp/store.py", "action": "modify", "description": "Add NoteStore.update"},
        {"path": "toyapp/app.py", "action": "modify", "description": "Add the PUT route"},
        {"path": "tests/test_app.py", "action": "modify", "description": "Add tests"},
    ],
    "acceptance_criteria": [
        "PUT /notes/<id> with a valid text returns 200 and the updated note (text stripped)",
        "PUT /notes/<id> returns 404 with an error body when the note does not exist",
        "PUT /notes/<id> returns 400 with an error body when text is missing, empty or not a string",
        "Tests cover success, 404 and invalid input",
    ],
    "risks": [],
}
SEARCH_SPEC = {
    "summary": "Make GET /notes accept ?q= and filter by case-insensitive substring",
    "changes": [
        {"path": "toyapp/app.py", "action": "modify", "description": "Filter in list_notes"},
        {"path": "tests/test_app.py", "action": "modify", "description": "Add tests"},
    ],
    "acceptance_criteria": [
        "GET /notes?q=milk returns notes whose text contains 'milk', ignoring case",
        "No q parameter (or an empty one) returns every note",
        "A query with no matches returns an empty list",
        "Tests cover these cases",
    ],
    "risks": [],
}

UPDATE_TESTS = [
    TEST_OLD,
    "",
    "",
    "def test_update_note(client):",
    '    client.post("/notes", json={"text": "old"})',
    '    res = client.put("/notes/1", json={"text": " new "})',
    "    assert res.status_code == 200",
    '    assert res.get_json() == {"id": 1, "text": "new"}',
    "",
    "",
    "def test_update_missing_note(client):",
    '    res = client.put("/notes/9", json={"text": "x"})',
    "    assert res.status_code == 404",
    '    assert "error" in res.get_json()',
    "",
    "",
    '@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "  "}, {"text": 5}])',
    "def test_update_rejects_bad_input(client, body):",
    '    client.post("/notes", json={"text": "keep"})',
    '    res = client.put("/notes/1", json=body)',
    "    assert res.status_code == 400",
    '    assert "error" in res.get_json()',
]
GOOD_UPDATE_OPS = [
    REFERENCE["update-note"][0],
    REFERENCE["update-note"][1],
    {"op": "replace", "path": "tests/test_app.py", "old": TEST_OLD, "new": UPDATE_TESTS},
]
SEARCH_TESTS = [
    TEST_OLD,
    "",
    "",
    "def test_search_filters_case_insensitively(client):",
    '    for text in ["Buy MILK", "walk dog"]:',
    '        client.post("/notes", json={"text": text})',
    '    assert [n["id"] for n in client.get("/notes?q=milk").get_json()] == [1]',
    '    assert client.get("/notes?q=zebra").get_json() == []',
    '    assert len(client.get("/notes?q=").get_json()) == 2',
    '    assert len(client.get("/notes").get_json()) == 2',
]
GET_STORE_OP, GET_ROUTE_OP, GET_TEST_OP = REVIEW[1]["operations"]

REVIEW += [
    {
        "id": "holdout-good-update",
        "description": "Correct PUT with validation and full tests (control: should be approved)",
        "expected": "approved",
        "tags": ["holdout", "control"],
        "spec": UPDATE_SPEC,
        "operations": GOOD_UPDATE_OPS,
    },
    {
        "id": "holdout-good-search",
        "description": "Correct case-insensitive search with tests (control: should be approved)",
        "expected": "approved",
        "tags": ["holdout", "control"],
        "spec": SEARCH_SPEC,
        "operations": [
            REFERENCE["search-notes"][0],
            {"op": "replace", "path": "tests/test_app.py", "old": TEST_OLD, "new": SEARCH_TESTS},
        ],
    },
    {
        "id": "holdout-bad-silent-behaviour-change",
        "description": "Correct GET /notes/<id>, but also silently truncates new note text to 100 chars",
        "expected": "changes_requested",
        "tags": ["holdout", "scope", "tests-pass"],
        "spec": REVIEW[1]["spec"],
        "operations": [
            GET_STORE_OP,
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": APP_OLD,
                "new": [
                    "        return jsonify(store.add(text.strip()[:100])), 201",
                    *GET_ROUTE_OP["new"][1:],
                ],
            },
            GET_TEST_OP,
        ],
    },
    {
        "id": "holdout-bad-weakened-existing-assertion",
        "description": "Correct PUT with tests, but loosens an existing assertion (no longer checks stripping)",
        "expected": "changes_requested",
        "tags": ["holdout", "tests-weakened", "tests-pass"],
        "spec": UPDATE_SPEC,
        "operations": [
            *GOOD_UPDATE_OPS,
            {
                "op": "replace",
                "path": "tests/test_app.py",
                "old": '    assert res.get_json() == {"id": 1, "text": "buy milk"}',
                "new": '    assert res.get_json()["id"] == 1',
            },
        ],
    },
    {
        "id": "holdout-bad-missing-validation",
        "description": "PUT accepts empty or non-string text; the 400 case is neither implemented nor tested",
        "expected": "changes_requested",
        "tags": ["holdout", "spec-violation", "missing-tests", "tests-pass"],
        "spec": UPDATE_SPEC,
        "operations": [
            REFERENCE["update-note"][0],
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": APP_OLD,
                "new": [
                    APP_OLD,
                    "",
                    '    @app.put("/notes/<int:note_id>")',
                    "    def update_note(note_id):",
                    "        body = request.get_json(silent=True) or {}",
                    '        note = store.update(note_id, body.get("text"))',
                    "        if note is None:",
                    '            return jsonify(error="note not found"), 404',
                    "        return jsonify(note)",
                ],
            },
            {
                "op": "replace",
                "path": "tests/test_app.py",
                "old": TEST_OLD,
                "new": [
                    TEST_OLD,
                    "",
                    "",
                    "def test_update_note(client):",
                    '    client.post("/notes", json={"text": "old"})',
                    '    res = client.put("/notes/1", json={"text": "new"})',
                    "    assert res.status_code == 200",
                    '    assert res.get_json()["text"] == "new"',
                    "",
                    "",
                    "def test_update_missing_note(client):",
                    '    assert client.put("/notes/9", json={"text": "x"}).status_code == 404',
                ],
            },
        ],
    },
    {
        "id": "holdout-bad-case-sensitive-search",
        "description": "Search is case-sensitive although the spec says case-insensitive; tests use lowercase only",
        "expected": "changes_requested",
        "tags": ["holdout", "correctness", "tests-pass"],
        "spec": SEARCH_SPEC,
        "operations": [
            {
                "op": "replace",
                "path": "toyapp/app.py",
                "old": LIST_OLD,
                "new": [
                    '        q = request.args.get("q", "")',
                    '        return jsonify([n for n in store.list() if q in n["text"]])',
                ],
            },
            {
                "op": "replace",
                "path": "tests/test_app.py",
                "old": TEST_OLD,
                "new": [
                    TEST_OLD,
                    "",
                    "",
                    "def test_search_notes(client):",
                    '    for text in ["buy milk", "walk dog"]:',
                    '        client.post("/notes", json={"text": text})',
                    '    assert [n["id"] for n in client.get("/notes?q=milk").get_json()] == [1]',
                    '    assert len(client.get("/notes").get_json()) == 2',
                ],
            },
        ],
    },
]


def main() -> None:
    for case in CODING:
        case["reference"] = REFERENCE[case["id"]]
    suite = {"name": "toy-notes-v1", "cases": CODING, "review_cases": REVIEW}
    (HERE / "suite.json").write_text(json.dumps(suite, indent=2) + "\n", encoding="utf-8")
    print(f"wrote suite.json: {len(CODING)} coding, {len(REVIEW)} review cases")


if __name__ == "__main__":
    main()
