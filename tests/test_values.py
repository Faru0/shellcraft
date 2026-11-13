"""The values a variable can hold besides text: arrays and dicts (shellcraft.values)."""

import pytest

from shellcraft import values
from shellcraft.values import PyValue, ShArray, ShMap, ValueProblem


def test_an_array_is_text_and_its_elements():
    array = ShArray(["x", "y z", 3])
    assert array == "x" and isinstance(array, str)  # `$a` is the first element, as in bash
    assert array.items == ("x", "y z", "3") and isinstance(array.items[2], PyValue)
    assert array.to_python() == ["x", "y z", 3]  # numbers stay numbers for Python code
    assert ShArray() == "" and ShArray().items == ()
    assert (array.get(0), array.get(-1), array.get(3), array.get(-4)) == ("x", "3", None, None)


def test_changing_an_array_makes_a_new_one():
    array = ShArray(["x", "y"])
    assert array.with_item(1, "Y").items == ("x", "Y") and array.items == ("x", "y")
    assert array.with_item(2, "z").items == ("x", "y", "z")  # one past the end appends
    assert array.with_item(-1, "L").items == ("x", "L")
    with pytest.raises(ValueProblem, match=r"hosts\[5\]: arrays have no gaps \(next index is 2\)"):
        array.with_item(5, "q", "hosts")
    with pytest.raises(ValueProblem, match="no such element"):
        array.with_item(-3, "q")
    assert array.without(1).items == ("x",) and array.without(-1).items == ("x",)
    assert array.without(7) is array  # nothing there: nothing to do
    with pytest.raises(ValueProblem, match=r"a\[0\]: would leave a gap"):
        array.without(0, "a")


def test_a_dict_keeps_its_order_and_takes_text_keys():
    held = ShMap({"web": 80, "db": "5432"})
    assert held == "" and held.items == (("web", "80"), ("db", "5432"))
    assert held.to_python() == {"web": 80, "db": "5432"}
    assert ShMap([("0", "zero")]) == "zero"  # `$m` is the value at key 0, as in bash
    assert held.get("web") == "80" and held.get("nope") is None
    assert held.with_item("web", "443").items == (("web", "443"), ("db", "5432"))  # a key keeps its place
    assert held.with_item("new", "1").items[-1] == ("new", "1")
    assert held.without("web").items == (("db", "5432"),) and held.without("nope").items == held.items
    with pytest.raises(ValueProblem, match=r'dict keys must be text \("1", not 1\)'):
        ShMap({1: "x"})
    with pytest.raises(ValueProblem, match="dict keys must be text"):
        ShMap([(PyValue(2), "x")])


@pytest.mark.parametrize("inner", [["a"], ("a",), {"k": 1}, {"a"}, ShArray(["a"]), ShMap({"k": "v"})])
def test_nothing_nests(inner):
    with pytest.raises(ValueProblem, match="only text and numbers, not another list or dict"):
        ShArray(["x", inner])
    with pytest.raises(ValueProblem, match="only text and numbers"):
        ShMap({"k": inner})


def test_other_things_are_refused_and_sizes_are_capped(monkeypatch):
    with pytest.raises(ValueProblem, match="not None"):
        ShArray([None])
    monkeypatch.setattr(values, "MAX_SEQUENCE", 3)
    with pytest.raises(ValueProblem, match="3 elements at most"):
        ShArray("abcd")
    monkeypatch.setattr(values, "MAX_TEXT", 5)
    with pytest.raises(ValueProblem, match="too large"):
        ShArray(["abc", "def"])
    with pytest.raises(ValueProblem, match="too large"):
        ShMap({"abc": "def"})


def test_elements_and_keys_of_any_value():
    assert values.elements(ShArray(["x", "y"])) == ["x", "y"] and values.keys(ShArray(["x", "y"])) == ["0", "1"]
    assert values.elements(ShMap({"a": "1"})) == ["1"] and values.keys(ShMap({"a": "1"})) == ["a"]
    assert values.elements("text") == ["text"] and values.keys("text") == ["0"]  # plain text: an array of one
    assert values.is_container(ShArray()) and values.is_container(ShMap()) and not values.is_container("x")
    assert values.to_python(PyValue(5)) == 5 and values.to_python("x") == "x"
    assert values.to_python(ShArray([1])) == [1]


def test_how_they_are_printed():
    array = ShArray(["x", "y z", "it's", ""])
    assert values.literal(array) == "(x 'y z' 'it'\\''s' '')"  # as you would type it
    assert values.declared(array) == '([0]="x" [1]="y z" [2]="it\'s" [3]="")'  # as `declare -p` prints it
    held = ShMap({"web": "80", "my key": 'a "b" $c'})
    assert values.literal(held) == "([web]=80 ['my key']='a \"b\" $c')"
    assert values.declared(held) == '([web]="80" [\'my key\']="a \\"b\\" \\$c")'
    assert values.literal("plain") == "plain" and values.declared("two words") == '"two words"'
