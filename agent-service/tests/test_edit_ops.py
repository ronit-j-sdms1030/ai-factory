"""Path-based edit application.

Security-sensitive: these operations come from a model, so a malformed or
hostile path must fail loudly rather than mutate something unintended.
"""

import pytest

from app.edit_ops import EditPathError, apply_edit_operation, normalize_operation


class TestApply:
    def test_sets_a_top_level_field(self):
        assert apply_edit_operation({"a": 1}, "a", 2) == {"a": 2}

    def test_sets_a_nested_field(self):
        got = apply_edit_operation({"a": {"b": {"c": 1}}}, "a.b.c", 9)
        assert got["a"]["b"]["c"] == 9

    def test_sets_a_list_element(self):
        got = apply_edit_operation({"xs": [{"v": 1}, {"v": 2}]}, "xs.1.v", 99)
        assert got["xs"][1]["v"] == 99

    def test_empty_path_replaces_the_whole_document(self):
        assert apply_edit_operation({"a": 1}, "", {"b": 2}) == {"b": 2}

    def test_adds_a_new_key_on_an_existing_object(self):
        assert apply_edit_operation({"a": {}}, "a.new", 1) == {"a": {"new": 1}}

    def test_does_not_mutate_the_original(self):
        original = {"a": {"b": 1}}
        apply_edit_operation(original, "a.b", 2)
        assert original == {"a": {"b": 1}}


class TestRejections:
    @pytest.mark.parametrize("path", ["__proto__", "constructor", "a.__proto__.b",
                                      "__class__", "a.__globals__", "prototype"])
    def test_unsafe_paths_are_refused(self, path):
        """Python has no prototype chain, but dunder paths still reach interpreter state."""
        with pytest.raises(EditPathError):
            apply_edit_operation({"a": {}}, path, "x")

    def test_empty_segment_is_refused(self):
        with pytest.raises(EditPathError):
            apply_edit_operation({"a": {}}, "a..b", 1)

    def test_missing_intermediate_key_is_refused(self):
        """Edits change existing structure; they do not invent it."""
        with pytest.raises(EditPathError):
            apply_edit_operation({"a": {}}, "nope.deep", 1)

    def test_descending_into_a_scalar_is_refused(self):
        with pytest.raises(EditPathError):
            apply_edit_operation({"a": 5}, "a.b", 1)

    def test_out_of_range_list_index_is_refused(self):
        with pytest.raises(EditPathError):
            apply_edit_operation({"xs": [1]}, "xs.7", 2)

    def test_non_numeric_list_index_is_refused(self):
        with pytest.raises(EditPathError):
            apply_edit_operation({"xs": [1]}, "xs.name", 2)


class TestNormalize:
    def test_splits_a_dotted_target(self):
        op = normalize_operation({"target": "detailedReport.objective", "value": "x"})
        assert op["target"] == "detailedReport"
        assert op["path"] == "objective"

    def test_joins_an_embedded_target_with_an_existing_path(self):
        op = normalize_operation({"target": "requirement.a", "path": "b", "value": 1})
        assert (op["target"], op["path"]) == ("requirement", "a.b")

    def test_plain_target_is_untouched(self):
        op = normalize_operation({"target": "title", "value": "T"})
        assert (op["target"], op["path"]) == ("title", "")

    def test_unknown_dotted_prefix_is_left_alone(self):
        op = normalize_operation({"target": "other.thing", "value": 1})
        assert op["target"] == "other.thing"
