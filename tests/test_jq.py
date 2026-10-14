"""jq: loading (JSON, JSON Lines, Python literals), the path syntax, -i / -k / -n / -c, errors,
no globbing on a jq line, and colored output on screen."""

import io
import json

import pytest
from rich.console import Console

from shellcraft.commands.jq import WILDCARD, load, parse_path
from shellcraft.context import Styled, to_text
from shellcraft.parser import tokenize
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes

PEOPLE = [{"name": "ada", "age": 36, "tags": ["x"]}, {"name": "bob", "age": 2}, {"age": 5}]
DOC = {"name": "ada", "items": [{"id": 7}, {"id": 8}], "ok": True}


@pytest.fixture
def files(ctx, tmp_path):
    (tmp_path / "list.json").write_text(json.dumps(PEOPLE))
    (tmp_path / "obj.json").write_text(json.dumps(DOC))
    return ctx


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


def test_load_accepts_json_json_lines_and_python_literals():
    assert load('{"a": 1}') == {"a": 1}
    assert load('{"a": 1}\n{"a": 2}\n') == [{"a": 1}, {"a": 2}]
    assert load("{'a': True, 'b': None, 'c': (1, 2)}") == {"a": True, "b": None, "c": (1, 2)}
    with pytest.raises(Exception, match="not valid JSON"):
        load("{nope")
    with pytest.raises(Exception, match="no input"):
        load("  ")


def test_parse_path_forms():
    assert parse_path("['name']") == ["name"]
    assert parse_path("[name]") == ["name"]  # the shell took the quotes away
    assert parse_path("[5]['name']") == [5, "name"]
    assert parse_path(".items[0].id") == ["items", 0, "id"]
    assert parse_path("items.0.id") == ["items", 0, "id"] and parse_path("a.b") == ["a", "b"]
    assert parse_path("[*][name]") == [WILDCARD, "name"]
    assert parse_path("[1:3]") == [slice(1, 3)]
    assert parse_path('["two words"]') == ["two words"]
    assert parse_path("") == [] and parse_path(".") == []
    with pytest.raises(Exception, match="bad filter"):
        parse_path("[0]!!")


def test_pretty_print_keys_and_filters(files):
    assert json.loads(out("cat obj.json | jq", files)) == DOC
    assert out("cat obj.json | jq", files).startswith('{\n  "name": "ada"')
    assert out("cat obj.json | jq --keys", files) == "name\nitems\nok\n"
    assert out("cat obj.json | jq --filter ['name']", files) == "ada\n"
    assert out("cat obj.json | jq -f ['name']", files) == "ada\n"
    assert out("jq -r obj.json --filter ['name']", files) == "ada\n"
    assert out("jq obj.json -f .items[1].id", files) == "8\n"
    assert out("jq obj.json -f ['ok']", files) == "true\n"
    assert out("jq obj.json -f ['items'] -c", files) == '[{"id":7},{"id":8}]\n'
    assert out("jq obj.json -f ['items'][*]['id']", files) == "7\n8\n"
    assert out("jq obj.json -n -f items", files) == "2\n"


def test_lists_with_index_and_wildcard(files):
    assert out("cat list.json | jq -f [1]['name']", files) == "bob\n"
    assert out("cat list.json | jq -i 0 -f ['name']", files) == "ada\n"
    assert out("cat list.json | jq -i -1 -f age", files) == "5\n"
    assert out("cat list.json | jq -i * -f ['name']", files) == "ada\nbob\n"  # the third has no name: skipped
    assert out("cat list.json | jq -i all -f ['name'] | sort -r", files) == "bob\nada\n"
    assert out("cat list.json | jq -i * --keys", files) == "[0] name, age, tags\n[1] name, age\n[2] age\n"
    assert out("cat list.json | jq -k", files) == "0\n1\n2\n"
    assert out("cat list.json | jq -n", files) == "3\n"
    assert out("cat list.json | jq -i * -f name -n", files) == "2\n"
    assert out("cat list.json | jq -i 0:2 -c", files) == '[{"name":"ada","age":36,"tags":["x"]},{"name":"bob","age":2}]\n'


def test_errors(files):
    with pytest.raises(PipelineError, match=r"no key 'nope' \(keys: name, items, ok\)"):
        run_line("jq obj.json -f ['nope']", files)
    with pytest.raises(PipelineError, match="index 9 out of range"):
        run_line("jq list.json -f [9]", files)
    with pytest.raises(PipelineError, match="on a list of 3"):
        run_line("jq list.json -f name", files)
    with pytest.raises(PipelineError, match="no key 'zzz'"):
        run_line("jq list.json -i * -f zzz", files)
    with pytest.raises(PipelineError, match="has no keys"):
        run_line("jq obj.json -f name -k", files)
    with pytest.raises(PipelineError, match="not both"):
        run_line("jq obj.json -r list.json", files)
    with pytest.raises(PipelineError, match="no input"):
        run_line("echo | jq", files)


def test_a_jq_line_is_not_globbed(files):
    words = [t.value for t in tokenize("cat list.json | jq -i * -f [name]", glob=True) if not t.op]
    assert words[-4:] == ["-i", "*", "-f", "[name]"]
    assert out("cat list.json | jq -i * -f [name]", files) == "ada\nbob\n"  # through the whole shell
    globbed = [t.value for t in tokenize("cat *.json | jq", glob=True) if not t.op]
    assert "list.json" in globbed and "obj.json" in globbed  # other commands still glob


def test_colored_on_screen_plain_in_pipes(files):
    files.interactive = True
    files.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    result = run_line("jq obj.json", files).output
    assert isinstance(result, Styled) and json.loads(to_text(result)) == DOC
    assert out("jq obj.json | cat", files).startswith("{\n")
