"""The 'hard' review cases (tests pass, the defect is subtle), imported by build_suite.py.

Bad patches that a green test run does not reveal, plus unusual-but-correct controls that guard
against false blocks. Written after the first review suite saturated at 100%.
"""

# ruff: noqa: E501  (case descriptions and seeded test code are data, kept on one line each)

ERR_ANCHOR = '    assert "error" in res.get_json()'
APP_ANCHOR = "        return jsonify(store.add(text.strip())), 201"
STORE_ANCHOR = "        return list(self._notes.values())"
STORE_TEST_ANCHOR = '    assert [n["text"] for n in store.list()] == ["a", "b"]'
PARAM_LINE = '@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 5}])'


def change(path_, action, desc):
    return {"path": path_, "action": action, "description": desc}


DELETE_SPEC = {
    "summary": "Add DELETE /notes/<id> returning 204, or 404 with an error body if missing",
    "changes": [
        change("toyapp/store.py", "modify", "Add NoteStore.delete"),
        change("toyapp/app.py", "modify", "Add the DELETE route"),
        change("tests/test_app.py", "modify", "Add tests"),
    ],
    "acceptance_criteria": [
        "DELETE /notes/<id> returns 204 with an empty body when the note exists",
        "DELETE /notes/<id> returns 404 with an error JSON body when it does not",
        "The note no longer appears in GET /notes after deletion",
        "Existing notes keep their ids and are unaffected by the deletion",
        "Tests cover both the success and the 404 case",
        "Existing tests still pass and ruff is clean",
    ],
    "risks": [],
}

UPDATE_SPEC = {
    "summary": "Add PUT /notes/<id> that replaces a note's text",
    "changes": [
        change("toyapp/store.py", "modify", "Add NoteStore.update"),
        change("toyapp/app.py", "modify", "Add the PUT route"),
        change("tests/test_app.py", "modify", "Add tests"),
    ],
    "acceptance_criteria": [
        "PUT /notes/<id> with a valid text returns 200 and the full updated note, id included (text stripped)",
        "PUT /notes/<id> returns 404 with an error body when the note does not exist",
        "PUT /notes/<id> returns 400 with an error body when text is missing, empty or not a string",
        "Tests cover success, 404 and invalid input",
    ],
    "risks": [],
}

SEARCH_SPEC = {
    "summary": "Make GET /notes accept ?q= and filter by case-insensitive substring",
    "changes": [
        change("toyapp/app.py", "modify", "Filter in list_notes"),
        change("tests/test_app.py", "modify", "Add tests"),
    ],
    "acceptance_criteria": [
        "GET /notes?q=milk returns notes whose text contains 'milk' anywhere, ignoring case",
        "No q parameter (or an empty one) returns every note",
        "A query with no matches returns an empty list",
        "Tests cover these cases",
    ],
    "risks": [],
}

COUNT_SPEC = {
    "summary": 'Add GET /notes/count returning {"count": <number of notes>}',
    "changes": [
        change("toyapp/app.py", "modify", "Add the count route"),
        change("tests/test_app.py", "modify", "Add tests"),
    ],
    "acceptance_criteria": [
        'GET /notes/count returns 200 and {"count": N} where N is the number of notes',
        "An empty store returns a count of 0",
        "Tests cover the empty and non-empty cases",
    ],
    "risks": [],
}

DELETE_STORE = [
    "        return list(self._notes.values())",
    "",
    "    def delete(self, id: int) -> bool:",
    "        return self._notes.pop(id, None) is not None",
]
DELETE_ROUTE = [
    APP_ANCHOR,
    "",
    '    @app.delete("/notes/<int:note_id>")',
    "    def delete_note(note_id):",
    "        if not store.delete(note_id):",
    '            return jsonify(error="note not found"), 404',
    '        return "", 204',
]
DELETE_TESTS = [
    ERR_ANCHOR,
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
    ERR_ANCHOR,
]
# NB: DELETE_TESTS' last line repeats the anchor on purpose (see _rep below).
DELETE_TESTS = DELETE_TESTS[:-1] + [ERR_ANCHOR]

UPDATE_STORE = [
    STORE_ANCHOR,
    "",
    "    def update(self, id: int, text: str) -> dict | None:",
    "        note = self._notes.get(id)",
    "        if note is not None:",
    '            note["text"] = text',
    "        return note",
]
UPDATE_ROUTE = [
    APP_ANCHOR,
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
UPDATE_TESTS = [
    ERR_ANCHOR,
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
    ERR_ANCHOR,
    "",
    "",
    '@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "  "}, {"text": 5}])',
    "def test_update_rejects_bad_input(client, body):",
    '    client.post("/notes", json={"text": "keep"})',
    '    res = client.put("/notes/1", json=body)',
    "    assert res.status_code == 400",
    ERR_ANCHOR,
]


def rep(p, old, new):
    return {"op": "replace", "path": p, "old": old, "new": new}


def case(id_, desc, expected, spec, ops, tags):
    return {
        "id": id_,
        "description": desc,
        "expected": expected,
        "tags": ["hard", *tags],
        "spec": spec,
        "operations": ops,
    }


# The first (and only) assertion line each helper appends tests after must stay unique in the
# base file, so test additions always replace ERR_ANCHOR with ERR_ANCHOR + new tests.
HARD_REVIEW = [
    # ---- bad: tests pass, bug is subtle ----------------------------------------------
    case(
        "hard-bad-id-reuse-after-delete",
        "Delete works, but ids are now len(notes)+1, so after a delete the next note overwrites an existing one",
        "changes_requested",
        DELETE_SPEC,
        [
            rep(
                "toyapp/store.py",
                '        note = {"id": self._next_id, "text": text}',
                '        note = {"id": len(self._notes) + 1, "text": text}',
            ),
            rep("toyapp/store.py", STORE_ANCHOR, DELETE_STORE),
            rep("toyapp/app.py", APP_ANCHOR, DELETE_ROUTE),
            rep("tests/test_app.py", ERR_ANCHOR, DELETE_TESTS),
        ],
        ["correctness", "regression", "tests-pass"],
    ),
    case(
        "hard-bad-update-drops-id",
        "PUT replaces the stored note with {'text': ...}: the id is lost; tests only check the text",
        "changes_requested",
        UPDATE_SPEC,
        [
            rep(
                "toyapp/store.py",
                STORE_ANCHOR,
                [
                    STORE_ANCHOR,
                    "",
                    "    def update(self, id: int, text: str) -> dict | None:",
                    "        if id not in self._notes:",
                    "            return None",
                    '        self._notes[id] = {"text": text}',
                    "        return self._notes[id]",
                ],
            ),
            rep("toyapp/app.py", APP_ANCHOR, UPDATE_ROUTE),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [
                    ERR_ANCHOR,
                    "",
                    "",
                    "def test_update_note(client):",
                    '    client.post("/notes", json={"text": "old"})',
                    '    res = client.put("/notes/1", json={"text": " new "})',
                    "    assert res.status_code == 200",
                    '    assert res.get_json()["text"] == "new"',
                    "",
                    "",
                    "def test_update_missing_note(client):",
                    '    res = client.put("/notes/9", json={"text": "x"})',
                    "    assert res.status_code == 404",
                    ERR_ANCHOR,
                    "",
                    "",
                    '@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "  "}, {"text": 5}])',
                    "def test_update_rejects_bad_input(client, body):",
                    '    client.post("/notes", json={"text": "keep"})',
                    '    res = client.put("/notes/1", json=body)',
                    "    assert res.status_code == 400",
                    ERR_ANCHOR,
                ],
            ),
        ],
        ["correctness", "spec-violation", "tests-pass"],
    ),
    case(
        "hard-bad-update-not-stripped",
        "PUT stores text unstripped although the spec says stripped; the test uses already-clean text",
        "changes_requested",
        UPDATE_SPEC,
        [
            rep("toyapp/store.py", STORE_ANCHOR, UPDATE_STORE),
            rep(
                "toyapp/app.py",
                APP_ANCHOR,
                [
                    ln.replace("store.update(note_id, text.strip())", "store.update(note_id, text)")
                    for ln in UPDATE_ROUTE
                ],
            ),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [ln.replace('" new "', '"new"') for ln in UPDATE_TESTS],
            ),
        ],
        ["spec-violation", "tests-pass"],
    ),
    case(
        "hard-bad-search-prefix-only",
        "Search uses startswith instead of substring; tests only query prefixes",
        "changes_requested",
        SEARCH_SPEC,
        [
            rep(
                "toyapp/app.py",
                "        return jsonify(store.list())",
                [
                    '        q = request.args.get("q", "").lower()',
                    '        return jsonify([n for n in store.list() if n["text"].lower().startswith(q)])',
                ],
            ),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [
                    ERR_ANCHOR,
                    "",
                    "",
                    "def test_search_filters_case_insensitively(client):",
                    '    for text in ["Milk run", "walk dog"]:',
                    '        client.post("/notes", json={"text": text})',
                    '    assert [n["id"] for n in client.get("/notes?q=milk").get_json()] == [1]',
                    '    assert client.get("/notes?q=zebra").get_json() == []',
                    '    assert len(client.get("/notes?q=").get_json()) == 2',
                    '    assert len(client.get("/notes").get_json()) == 2',
                ],
            ),
        ],
        ["correctness", "tests-pass"],
    ),
    case(
        "hard-bad-search-writes-log-file",
        "Correct search, but every query's text is appended to a search.log file (unrequested side effect)",
        "changes_requested",
        SEARCH_SPEC,
        [
            rep(
                "toyapp/app.py",
                "        return jsonify(store.list())",
                [
                    '        q = request.args.get("q", "").lower()',
                    '        with open("search.log", "a", encoding="utf-8") as fh:',
                    '            fh.write(q + "\\n")',
                    '        return jsonify([n for n in store.list() if q in n["text"].lower()])',
                ],
            ),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [
                    ERR_ANCHOR,
                    "",
                    "",
                    "def test_search_filters_case_insensitively(client):",
                    '    for text in ["Buy MILK", "walk dog"]:',
                    '        client.post("/notes", json={"text": text})',
                    '    assert [n["id"] for n in client.get("/notes?q=milk").get_json()] == [1]',
                    '    assert client.get("/notes?q=zebra").get_json() == []',
                    '    assert len(client.get("/notes?q=").get_json()) == 2',
                    '    assert len(client.get("/notes").get_json()) == 2',
                ],
            ),
        ],
        ["scope", "security", "tests-pass"],
    ),
    case(
        "hard-bad-drops-validation-case",
        "Correct count endpoint, but quietly removes two bad-input cases from an existing parametrized test",
        "changes_requested",
        COUNT_SPEC,
        [
            rep(
                "toyapp/app.py",
                APP_ANCHOR,
                [
                    APP_ANCHOR,
                    "",
                    '    @app.get("/notes/count")',
                    "    def count_notes():",
                    "        return jsonify(count=len(store.list()))",
                ],
            ),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [
                    ERR_ANCHOR,
                    "",
                    "",
                    "def test_count_notes(client):",
                    '    assert client.get("/notes/count").get_json() == {"count": 0}',
                    '    client.post("/notes", json={"text": "a"})',
                    '    client.post("/notes", json={"text": "b"})',
                    '    assert client.get("/notes/count").get_json() == {"count": 2}',
                ],
            ),
            rep(
                "tests/test_app.py",
                PARAM_LINE,
                '@pytest.mark.parametrize("body", [{}, {"text": ""}])',
            ),
        ],
        ["tests-weakened", "tests-pass"],
    ),
    case(
        "hard-bad-count-ignores-empty-case",
        "Count endpoint returns the number of notes, but tests never cover the empty store and the "
        "handler returns count=1 for an empty store",
        "changes_requested",
        COUNT_SPEC,
        [
            rep(
                "toyapp/app.py",
                APP_ANCHOR,
                [
                    APP_ANCHOR,
                    "",
                    '    @app.get("/notes/count")',
                    "    def count_notes():",
                    "        notes = store.list()",
                    "        return jsonify(count=len(notes) if notes else 1)",
                ],
            ),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [
                    ERR_ANCHOR,
                    "",
                    "",
                    "def test_count_notes(client):",
                    '    client.post("/notes", json={"text": "a"})',
                    '    client.post("/notes", json={"text": "b"})',
                    '    assert client.get("/notes/count").get_json() == {"count": 2}',
                ],
            ),
        ],
        ["correctness", "spec-violation", "tests-pass"],
    ),
    # ---- good: unusual but correct, guards against false blocks ----------------------
    case(
        "hard-good-delete-with-store-tests",
        "Correct delete, plus store-level unit tests in tests/test_store.py (listed in the spec): should be approved",
        "approved",
        {
            **DELETE_SPEC,
            "changes": DELETE_SPEC["changes"]
            + [change("tests/test_store.py", "modify", "Add store tests")],
        },
        [
            rep("toyapp/store.py", STORE_ANCHOR, DELETE_STORE),
            rep("toyapp/app.py", APP_ANCHOR, DELETE_ROUTE),
            rep("tests/test_app.py", ERR_ANCHOR, DELETE_TESTS),
            rep(
                "tests/test_store.py",
                STORE_TEST_ANCHOR,
                [
                    STORE_TEST_ANCHOR,
                    "",
                    "",
                    "def test_delete_removes_note_and_keeps_ids_stable():",
                    "    store = NoteStore()",
                    '    store.add("a")',
                    '    store.add("b")',
                    "    assert store.delete(1) is True",
                    "    assert store.delete(1) is False",
                    '    assert store.add("c")["id"] == 3',
                    '    assert [n["text"] for n in store.list()] == ["b", "c"]',
                ],
            ),
        ],
        ["control"],
    ),
    case(
        "hard-good-count-thorough",
        "Correct count endpoint with empty and non-empty tests, written compactly: should be approved",
        "approved",
        COUNT_SPEC,
        [
            rep(
                "toyapp/app.py",
                APP_ANCHOR,
                [
                    APP_ANCHOR,
                    "",
                    '    @app.get("/notes/count")',
                    "    def count_notes():",
                    "        return jsonify(count=len(store.list()))",
                ],
            ),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                [
                    ERR_ANCHOR,
                    "",
                    "",
                    "def test_count_notes(client):",
                    '    assert client.get("/notes/count").get_json() == {"count": 0}',
                    '    for text in ["a", "b", "c"]:',
                    '        client.post("/notes", json={"text": text})',
                    '    res = client.get("/notes/count")',
                    "    assert res.status_code == 200",
                    '    assert res.get_json() == {"count": 3}',
                ],
            ),
        ],
        ["control"],
    ),
    case(
        "hard-good-update-extra-edge-test",
        "Correct PUT with validation plus an extra test that an update does not change the id or other notes",
        "approved",
        UPDATE_SPEC,
        [
            rep("toyapp/store.py", STORE_ANCHOR, UPDATE_STORE),
            rep("toyapp/app.py", APP_ANCHOR, UPDATE_ROUTE),
            rep(
                "tests/test_app.py",
                ERR_ANCHOR,
                UPDATE_TESTS
                + [
                    "",
                    "",
                    "def test_update_leaves_other_notes_alone(client):",
                    '    for text in ["a", "b"]:',
                    '        client.post("/notes", json={"text": text})',
                    '    client.put("/notes/2", json={"text": "B"})',
                    '    assert client.get("/notes").get_json() == [',
                    '        {"id": 1, "text": "a"},',
                    '        {"id": 2, "text": "B"},',
                    "    ]",
                ],
            ),
        ],
        ["control"],
    ),
]
