import pytest

from intg_monoprice_htp1.device import apply_json_patch


def test_dictionary_add_replace_remove_and_json_pointer_escaping():
    state = {"a/b": {"tilde~key": 1}}

    apply_json_patch(state, "add", "/a~1b/new", 2)
    apply_json_patch(state, "replace", "/a~1b/tilde~0key", 3)
    apply_json_patch(state, "remove", "/a~1b/new")

    assert state == {"a/b": {"tilde~key": 3}}


def test_array_add_replace_remove_and_append():
    state = {"items": ["first", "third"]}

    apply_json_patch(state, "add", "/items/1", "second")
    apply_json_patch(state, "replace", "/items/0", "zero")
    apply_json_patch(state, "add", "/items/-", "last")
    apply_json_patch(state, "remove", "/items/2")

    assert state == {"items": ["zero", "second", "last"]}


@pytest.mark.parametrize(
    ("operation", "path", "value", "error"),
    [
        ("move", "/value", 1, ValueError),
        ("replace", "value", 1, ValueError),
        ("replace", "/missing", 1, KeyError),
        ("remove", "/items/4", None, IndexError),
        ("add", "/items/4", 1, IndexError),
        ("replace", "/items/-", 1, ValueError),
        ("replace", "/items/not-an-index", 1, ValueError),
        ("replace", "/bad~2escape", 1, ValueError),
    ],
)
def test_invalid_patch_operations_and_paths_are_rejected(operation, path, value, error):
    state = {"value": 1, "items": [1, 2]}

    with pytest.raises(error):
        apply_json_patch(state, operation, path, value)


def test_patch_rejects_scalar_intermediate_nodes():
    with pytest.raises(TypeError):
        apply_json_patch({"scalar": 1}, "replace", "/scalar/child", 2)


def test_patch_can_add_nested_dictionary_members():
    state = {"parent": {}}

    apply_json_patch(state, "add", "/parent/child", {"enabled": True})

    assert state["parent"]["child"] == {"enabled": True}
