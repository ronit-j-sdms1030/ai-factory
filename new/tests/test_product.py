"""Assembled product is a real schema + API + Gate 3 JSX."""

from phase4 import product, runtime_db
from phase4.build import materialise


def test_parse_entities_ignores_gherkin_and_screen_blurbs():
    brd = """
## Requirements
- **Source:** approved scope, in-scope item 1.
- **Acceptance criteria:**
- **LoginScreen**: Secure single authentication entry point.
- **StaffDashboard**: Overview panel for loans.

## Data model
- **User / Member / Staff:** id, name, email, role (staff/member), password_hash, created_at
- **Book:** id, title, author, isbn, status (available, on_loan, unavailable), created_at
- **Loan:** id, book_id, member_id, checked_out_at, due_date, returned_at, status (active, returned, overdue), created_at
- **ImportLog:** id, imported_by, file_name, records_processed, status, created_at
"""
    entities = product.parse_entities(brd)
    names = [name for name, _ in entities]
    assert names == ["User", "Book", "Loan", "ImportLog"]
    book_fields = dict(entities)["Book"]
    assert book_fields == ["id", "title", "author", "isbn", "status", "created_at"]
    server = product.express_server("REQ-0001", entities)
    assert "approved scope" not in server
    assert "books:" in server
    assert "loans:" in server
    assert "const approved" not in server
    assert "body.approved" not in server


def test_assemble_includes_ai_inside_the_same_app():
    screens = [{"name": "BookRoom", "source": "function BookRoom() { return <main />; }\n"}]
    files = product.assemble("REQ-0099", brd_text="- **Room:** id, name\n", screens=screens)
    assert "src/ai/infer.js" in "".join(files) or any(path.endswith("src/ai/infer.js") for path in files)
    assert "/api/ai/suggest" in files["app/REQ-0099/server.js"]
    assert "module.exports" in files["app/REQ-0099/src/ai/infer.js"]


def test_assemble_writes_db_api_and_exact_screens():
    screens = [
        {
            "name": "BookRoom",
            "source": (
                "function Page(props) { return <main>{props.children}</main>; }\n"
                "function BookRoom() { return ( <Page><h1>Book a room</h1></Page> ); }\n"
            ),
        }
    ]
    brd = "- **Room:** id, name\n- **Booking:** id, room_id, starts_at, ends_at\n"
    files = product.assemble("REQ-0099", brd_text=brd, screens=screens, profile_id="node")
    assert "model Room" in files["app/REQ-0099/prisma/schema.prisma"]
    assert "CREATE TABLE" in files["app/REQ-0099/prisma/migrations/0001_init/migration.sql"]
    assert "function BookRoom" in files["app/REQ-0099/public/index.html"]
    assert "data-theme" in files["app/REQ-0099/public/index.html"]
    html = files["app/REQ-0099/public/index.html"]
    assert "screen-switch" not in html
    assert "window.navigate" in html and "hashchange" in html
    assert "listen" in files["app/REQ-0099/server.js"]


def test_materialise_keeps_jsx_on_ui_ticket():
    screens = [
        {
            "name": "BookRoom",
            "source": "function BookRoom() { return ( <main>Book</main> ); }\n",
        }
    ]
    tickets = [
        {
            "id": "REQ-0099-W1",
            "title": "schema + migration, including the exclusion constraint",
            "department": "development",
            "paths": ["prisma/schema.prisma", "prisma/migrations/**"],
        },
        {
            "id": "REQ-0099-W5",
            "title": "BookRoom screen",
            "department": "development",
            "paths": ["src/ui/**"],
        },
        {
            "id": "REQ-0099-AI1",
            "title": "prompt + inference adapter for screen suggestions",
            "department": "ai",
            "paths": ["src/ai/**"],
        },
    ]
    built = materialise(
        "REQ-0099",
        tickets,
        screens=screens,
        brd_text="- **Room:** id, name\n",
        profile={"id": "node"},
    )
    schema_files = built["branches"]["feat/REQ-0099-W1"]
    assert "model Room" in schema_files["prisma/schema.prisma"]
    ui_files = built["branches"]["feat/REQ-0099-W5"]
    assert "function BookRoom" in ui_files["src/ui/BookRoom.jsx"]
    assert built["extra"]["app/REQ-0099/server.js"]
    assert "suggest" in built["extra"]["app/REQ-0099/src/ai/infer.js"]
    assert "export" in built["branches"]["feat/REQ-0099-AI1"]["src/ai/infer.ts"]


def test_runtime_db_round_trip(tmp_path):
    brd = "- **Room:** id, name\n"
    status, created = runtime_db.handle(
        tmp_path, "REQ-0099", brd, "POST", "rooms", body={"name": "A1"}
    )
    assert status == 201
    status, rows = runtime_db.handle(tmp_path, "REQ-0099", brd, "GET", "rooms")
    assert status == 200
    assert rows[0]["name"] == "A1"
