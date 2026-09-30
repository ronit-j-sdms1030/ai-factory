"""Demo IDE codegen job streaming."""

from phase1 import codegen


def test_plan_orders_ui_before_db():
    row = {
        "requirement": {"id": "REQ-0099"},
        "brd_text": "- **Book:** id, title, author\n- **Loan:** id, book_id, status\n",
        "screens": [
            {
                "name": "LoginScreen",
                "source": "function LoginScreen(){return <main><h1>Login</h1></main>;}\n",
            }
        ],
        "stack_profile": {"id": "node", "frontend": "React", "api": "Express"},
        "tickets": [{"id": "W1", "title": "schema", "department": "development"}],
    }
    files = codegen._plan_files(row, "development")
    paths = [f["path"] for f in files]
    assert any(p.endswith("LoginScreen.jsx") for p in paths)
    ui_i = next(i for i, p in enumerate(paths) if p.endswith(".jsx"))
    db_i = next(i for i, p in enumerate(paths) if "migration" in p or "schema.prisma" in p)
    assert ui_i < db_i


def test_status_streams_partials_then_done():
    row = {
        "requirement": {"id": "REQ-0098"},
        "brd_text": "- **Book:** id, title\n",
        "screens": [
            {
                "name": "Home",
                "source": "function Home(){return <main>Hi</main>;}\n",
            }
        ],
        "stack_profile": {"id": "node"},
        "tickets": [],
    }
    started = codegen.start(
        "REQ-0098",
        row=row,
        actor={"department": "development", "id": "u-tl"},
        model="factory/assembler",
    )
    cid = started["codeGenId"]
    first = codegen.status(cid)
    assert first["status"] == "generating"
    assert first["totalFiles"] >= 1
    with codegen._LOCK:
        job = codegen._JOBS[cid]
        job["startedAt"] = 0
    done = codegen.status(cid)
    assert done["status"] == "done"
    assert all(f["done"] for f in done["files"])
    sample = done["files"][0]["path"]
    payload = codegen.get_file(cid, sample)
    assert payload and payload["content"]


def test_start_after_gate4_starts_each_stream():
    row = {
        "requirement": {"id": "REQ-0097"},
        "brd_text": "- **Book:** id, title\n",
        "screens": [{"name": "Home", "source": "function Home(){return <main/>;}\n"}],
        "stack_profile": {"id": "node"},
        "tickets": [
            {"id": "W1", "title": "api", "department": "development", "paths": ["src/api/a.js"]},
            {"id": "W2", "title": "ai", "department": "ai", "paths": ["src/ai/b.js"]},
        ],
        "team_reports": [{"team": "development"}, {"team": "ai"}],
        "build": {"ok": True, "scans": {"ok": True}, "blocking": [], "findings": []},
    }
    started = codegen.start_after_gate4("REQ-0097", row)
    assert len(started) == 2
    by = codegen.by_artifact("REQ-0097")
    depts = {m["department"] for m in by["modules"]}
    assert depts == {"development", "ai"}
    assert all(m["status"] in {"generating", "done"} for m in by["modules"])


def test_models_endpoint_shape():
    out = codegen.models()
    assert out["models"]
    assert any(m.get("isDefault") for m in out["models"])
    assert any(m["id"] == "factory/assembler" for m in out["models"])
