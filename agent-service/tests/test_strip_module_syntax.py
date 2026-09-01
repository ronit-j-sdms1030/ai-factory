"""Removing module syntax from a generated screen.

The screens are assembled into one page where React is already global, so a
file-style `import React from 'react'` is fatal — "Cannot use import statement
outside a module", eleven times out of eleven, while the React itself was
perfectly good. The prompt already forbids it and a code-specialised model
does it anyway; models trained on real files write real files.
"""

from __future__ import annotations

from app.agents.ui import strip_module_syntax


class TestImports:
    def test_a_react_import_is_removed(self):
        assert "import" not in strip_module_syntax("import React from 'react';\nfunction A(){}")

    def test_named_imports_are_removed(self):
        src = "import { useState, useEffect } from 'react';\nfunction A(){}"
        assert strip_module_syntax(src).strip() == "function A(){}"

    def test_an_indented_import_is_still_removed(self):
        assert "import" not in strip_module_syntax("  import x from 'y';\nfunction A(){}")


class TestExports:
    def test_a_default_exported_function_keeps_its_declaration(self):
        """Deleting the line would take the component with it."""
        out = strip_module_syntax("export default function Dashboard() {\n  return null;\n}")
        assert out.startswith("function Dashboard()")
        assert "return null;" in out

    def test_a_default_exported_class_keeps_its_declaration(self):
        assert strip_module_syntax("export default class Foo {}").startswith("class Foo")

    def test_a_bare_default_export_is_dropped(self):
        out = strip_module_syntax("function Dashboard(){}\nexport default Dashboard;")
        assert "export" not in out
        assert "function Dashboard(){}" in out

    def test_a_named_export_keeps_its_declaration(self):
        assert strip_module_syntax("export const styles = { a: 1 };") == "const styles = { a: 1 };"


class TestFalsePositives:
    def test_a_string_mentioning_import_survives(self):
        src = 'const t = "import this"; const u = "export that";'
        assert strip_module_syntax(src) == src

    def test_a_comment_mentioning_import_survives(self):
        src = "// import the roster before rendering\nfunction A(){}"
        assert "// import the roster" in strip_module_syntax(src)

    def test_ordinary_source_is_returned_unchanged(self):
        src = "function A() {\n  const [x, setX] = React.useState(0);\n  return <div>{x}</div>;\n}"
        assert strip_module_syntax(src) == src
