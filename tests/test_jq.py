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


def test_where_filters_list_elements(files):
    assert out("jq list.json -w 'age>30' -f name", files) == "ada\n"
    assert out("jq list.json -w 'age<=5' -n", files) == "2\n"
    assert out("jq list.json -w name~AD", files).startswith("{\n")  # contains, any case
    assert out("jq list.json -w name -w age!=36 -f name", files) == "bob\n"  # both must match
    assert out("jq list.json -w name=bob -f age", files) == "2\n"
    assert out("jq list.json -w tags[0]=x -f name", files) == "ada\n"
    result = run_line("jq list.json -w age=999", files)
    assert result.status == 1 and to_text(result.output) == ""


def test_paths_map_the_document_and_feed_back_into_f(files):
    lines = out("jq obj.json -p", files).splitlines()
    assert lines == ['.name = "ada"', ".items[0].id = 7", ".items[1].id = 8", ".ok = true"]
    path = lines[2].split(" = ")[0]
    assert out(f"jq obj.json -f {path}", files) == "8\n"
    assert out("jq list.json -i 0 -p", files).splitlines()[0] == '.name = "ada"'
    from shellcraft.commands.jq import leaf_paths

    assert leaf_paths({"two words": [], "ok": {}}) == ['["two words"] = []', ".ok = {}"]


def test_table_plain_in_pipes_and_rich_on_screen(files):
    assert out("jq list.json -t", files) == "name\tage\ttags\nada\t36\t[\"x\"]\nbob\t2\t\n\t5\t\n"
    assert out("jq obj.json -t", files).startswith("key\tvalue\nname\tada\nitems\t[{")
    assert out("jq obj.json -f items[*].id -t", files) == "value\n7\n8\n"
    files.interactive = True
    files.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    shown = run_line("jq list.json -w name -t", files).output
    assert isinstance(shown, Styled) and to_text(shown).startswith("name\tage\ttags\nada")


def test_jq_tells_the_shape_and_takes_a_bare_path(files):
    from shellcraft.commands.jq import hints, shape

    assert shape(DOC) == "object with 3 keys: name, items, ok"
    assert hints(DOC) == "jq -k · jq -f name"  # dict -> list -> dict: not deep enough for the extras
    assert shape(PEOPLE) == "list of 3 objects (keys: name, age, tags)"
    assert hints(PEOPLE) == "jq [0] · jq -t · jq -i '*' -f name · jq -n"
    assert shape([1, 2, 3]) == "list of 3 numbers" and hints([1, 2, 3]) == "jq [0] · jq [2] · jq -n"
    assert shape(["a", 1]) == "list of 2 mixed values (number, string)" and shape([]) == "empty list"
    assert shape([[1], [2]]) == "list of 2 lists" and shape("x") == "string" and shape({}) == "object with 0 keys"
    assert out("jq list.json -s", files) == "list of 3 objects (keys: name, age, tags)\n"
    assert out("cat obj.json | jq --shape", files) == "object with 3 keys: name, items, ok\n"
    # a path on its own, like Python's indexing; a file and a path together
    assert out("cat list.json | jq [1]", files) == '{\n  "name": "bob",\n  "age": 2\n}\n'
    assert out("jq list.json [0][name]", files) == "ada\n" and out("jq obj.json .items[1].id", files) == "8\n"
    assert out("jq [0][name] list.json", files) == "ada\n"
    with pytest.raises(PipelineError, match="give the path once"):
        run_line("jq list.json [0] -f name", files)
    with pytest.raises(PipelineError, match="one path at a time"):
        run_line("jq list.json [0] [name]", files)
    # the header on screen only: a pipe gets bare JSON
    files.interactive = True
    files.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    shown = run_line("jq list.json", files).output
    assert isinstance(shown, Styled) and to_text(shown).startswith("[\n")  # the plain side: no header
    files.ui.console.print(shown.renderable)
    screen = files.ui.console.file.getvalue()
    assert "# list of 3 objects (keys: name, age, tags) · jq [0] · jq -t" in screen
    files.ui.console.file.truncate(0)
    files.ui.console.file.seek(0)
    files.ui.console.print(run_line("jq list.json [1]", files).output.renderable)  # a path: no header
    assert "# " not in files.ui.console.file.getvalue() and "bob" in files.ui.console.file.getvalue()
    assert out("jq list.json | cat", files).startswith("[\n")


def test_xml_becomes_json_style_data(files, tmp_path):
    from shellcraft.commands.jq import xml_to_data

    xml = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>News</title>
  <item id="1"><title>First</title><tags><tag>a</tag><tag>b</tag></tags></item>
  <item id="2"><title>Second</title><empty/></item>
  <note lang="en">hi <b>there</b></note>
</channel></rss>"""
    data = xml_to_data(xml)
    channel = data["rss"]["channel"]
    assert data["rss"]["@version"] == "2.0" and channel["title"] == "News"
    assert [i["@id"] for i in channel["item"]] == ["1", "2"] and channel["item"][0]["tags"]["tag"] == ["a", "b"]
    assert channel["item"][1]["empty"] is None and channel["note"] == {"@lang": "en", "#text": "hi", "b": "there"}
    (tmp_path / "feed.xml").write_text(xml)
    assert out("jq feed.xml -x -f .rss.channel.item[*].title", files) == "First\nSecond\n"
    assert out("cat feed.xml | jq -f rss.channel.title", files) == "News\n"  # a tag at the start: XML by itself
    shape_lines = out("jq feed.xml -x -s", files).splitlines()  # deep enough for the outline
    assert shape_lines[0].startswith("object{rss} -> rss: object{@version,channel} -> channel:")
    assert shape_lines[1].endswith("(7 levels)")
    assert '"@version":"2.0"' in out("jq feed.xml --xml -c", files)
    assert xml_to_data("<a xmlns='urn:x'><b>1</b></a>") == {"a": {"b": "1"}}  # namespaces dropped from tags
    with pytest.raises(Exception, match="not valid XML"):
        xml_to_data("<a><b></a>")


def test_less_and_more_page_on_request_and_pass_through_in_pipes(files, tmp_path):
    from shellcraft.context import Paged

    (tmp_path / "big.txt").write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
    assert out("cat big.txt | less | wc -l", files).strip() == "100"  # a pipe: the text passes through
    assert out("less big.txt | head -1", files) == "line 0\n"
    files.interactive = True
    files.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    shown = run_line("cat big.txt | less", files).output
    assert isinstance(shown, Paged) and shown.page is True and shown.value.startswith("line 0\n")
    assert isinstance(run_line("more big.txt", files).output, Paged)
    assert run_line("echo -n | less", files).output == ""
    with pytest.raises(PipelineError, match="usage: less"):
        run_line("less a b", files)


# ── deep nesting: dict -> list -> dict -> list … ─────────────────────────────

ORG = {"company": {"name": "Acme", "departments": [
    {"name": "eng", "teams": [
        {"name": "core", "members": [
            {"name": "ada", "skills": [{"name": "py", "level": 5}, {"name": "c", "level": 3}]},
            {"name": "bob", "skills": []}]},
        {"name": "web", "members": [{"name": "cy", "skills": [{"name": "js", "level": 4}]}]}]},
    {"name": "ops", "teams": [
        {"name": "sre", "members": [
            {"name": "di", "skills": [{"name": "go", "level": 4}],
             "manager": {"name": "eve", "skills": [{"name": "sh", "level": 2}]}}]}]}]}}
DEPT_ONE = "company.departments[1].teams[0].members[0]"


@pytest.fixture
def org(files, tmp_path):
    (tmp_path / "org.json").write_text(json.dumps(ORG))
    return files


def test_parse_path_recursive_descent_and_many_levels():
    from shellcraft.commands.jq import Recurse

    assert parse_path("..name") == [Recurse("name")] and parse_path("..") == [Recurse(None)]
    assert parse_path(".items..id") == ["items", Recurse("id")]
    assert parse_path("[*].items[*].tags[*]") == [WILDCARD, "items", WILDCARD, "tags", WILDCARD]
    assert parse_path("a..b[0]") == ["a", Recurse("b"), 0]
    assert Recurse("x") == Recurse("x") and Recurse("x") != Recurse(None)


def test_outline_names_the_nesting(org):
    from shellcraft.commands.jq import describe, nesting, spine

    assert nesting(ORG) == 11 and nesting("x") == 0 and nesting([]) == 1 and nesting(PEOPLE) == 3
    four = {"a": [{"b": [1, 2]}]}  # dict -> list -> dict -> list: the first level that counts as deep
    assert describe(four) == ["object{a} -> a: list of 1 object{b} -> b: list of 2 numbers",
                              "dict -> list[1] -> dict -> list[2]   (4 levels)"]
    assert describe(DOC) == ["object with 3 keys: name, items, ok"]
    lines = describe(ORG)
    assert lines[0].startswith("object{company} -> company: object{name,departments} -> departments: "
                               "list of 2 objects{name,teams} -> teams: list of 1 object{name,members}")
    assert lines[1].startswith("dict -> dict -> list[2] -> dict -> list[1] -> dict") and lines[1].endswith("(11 levels)")
    assert describe(PEOPLE) == ["list of 3 objects (keys: name, age, tags)"]  # shallow data: the usual line
    # a chain far deeper than the outline follows stops with …
    deep: dict = {}
    node = deep
    for _ in range(30):
        node["a"] = {}
        node = node["a"]
    outline, chain, levels, last = spine(deep)
    assert outline.endswith("-> …") and chain.endswith("-> …") and levels == 31 and outline.count("->") == 8
    # through the command: the data, after a filter, and many results
    assert out("jq org.json -s", org).splitlines()[0].startswith("object{company} -> company:")
    after = out("jq org.json -s -f .company.departments", org)
    assert after.startswith("list of 2 objects{name,teams} -> teams: list of 1 object{name,members}")
    many = out("jq org.json -s -R skills", org).splitlines()
    assert many[0] == ".company.departments[0].teams[0].members[0].skills: list of 2 objects (keys: name, level)"
    assert len(many) == 5


def test_find_a_key_at_any_depth(org):
    assert out("jq org.json -R level", org) == "5\n3\n4\n4\n2\n"
    assert out("jq org.json -f ..level", org) == "5\n3\n4\n4\n2\n"
    assert out("jq org.json ..level", org) == "5\n3\n4\n4\n2\n"  # a path on its own
    assert out("jq org.json -R name -n", org) == "16\n"
    assert out("jq org.json -f .company.departments[1] -R level", org) == "4\n2\n"  # a path, then the search
    assert out("jq org.json -R skills -n", org) == "5\n"
    first = out("jq org.json -f .. -c", org).splitlines()[0]
    assert first == json.dumps(ORG, separators=(",", ":"))  # `..` starts with the whole document
    assert out("jq obj.json .. -n", org) == "8\n"
    with pytest.raises(PipelineError, match="no key 'nme' at any depth; did you mean 'name'"):
        run_line("jq org.json -R nme", org)
    with pytest.raises(PipelineError, match="no key 'zzz' at any depth"):
        run_line("jq org.json ..zzz", org)


def test_with_paths_shows_where_every_match_lives(org):
    got = out("jq org.json -R level -P", org).splitlines()
    assert got[0] == ".company.departments[0].teams[0].members[0].skills[0].level = 5"
    assert got[-1] == f".{DEPT_ONE}.manager.skills[0].level = 2" and len(got) == 5
    names = out("jq org.json -f '.company.departments[*].name' -P", org)
    assert names == ".company.departments[0].name = eng\n.company.departments[1].name = ops\n"
    assert out("jq org.json -f .company.name -P", org) == ".company.name = Acme\n"
    containers = out("jq org.json -f .company.departments[1].teams -P -c", org)
    assert containers.startswith(".company.departments[1].teams = [{\"name\":\"sre\"")
    # -p: relative to the result, or from the root with -P (each path works as -f)
    relative = out("jq org.json -f .company.departments[1].teams -p", org).splitlines()
    absolute = out("jq org.json -f .company.departments[1].teams -p -P", org).splitlines()
    assert relative[0] == '[0].name = "sre"' and absolute[0] == '.company.departments[1].teams[0].name = "sre"'
    assert out(f"jq org.json -f {absolute[0].split(' = ')[0]}", org) == "sre\n"


def test_wildcards_through_many_levels_in_one_filter(org):
    names = "ada\nbob\ncy\ndi\n"
    assert out("jq org.json -f '.company.departments[*].teams[*].members[*].name'", org) == names
    assert out("jq org.json -f ..members[*].name", org) == names  # descent, then the lists
    skills = out("jq org.json -f '.company.departments[*].teams[*].members[*].skills[*].name'", org)
    assert skills == "py\nc\njs\ngo\n"
    assert out("jq org.json -f '.company.departments[*].teams[*].members[*].skills[*].level' -n", org) == "4\n"
    assert out("jq org.json -f '.company.departments[*].teams[*].members[*]' -n", org) == "4\n"


def test_where_any_matches_at_any_depth(org):
    assert out("jq org.json -W 'level>=4' -f name", org) == "py\njs\ngo\n"
    assert out("jq org.json -W 'level>=4' -W 'name~j' -f name", org) == "js\n"
    assert out("jq org.json -W 'name=sre' -f 'members[*].name'", org) == "di\n"
    assert out("jq org.json -W '~sre' -P", org) == ".company.departments[1].teams[0].name = sre\n"
    assert out("jq org.json -W '=eve' -P", org) == f".{DEPT_ONE}.manager.name = eve\n"
    assert out("jq org.json -W 'level' -n", org) == "5\n"  # objects that have the key
    assert out("jq org.json -W '>3'", org) == "5\n4\n4\n"  # scalar values over 3
    result = run_line("jq org.json -W 'name=nobody'", org)
    assert result.status == 1 and to_text(result.output) == ""
    outline = out("jq org.json -W 'level>=4' -k -d 1", org).splitlines()  # their key outline, from the root
    assert len(outline) == 6 and outline[0] == ".company.departments[0].teams[0].members[0].skills[0].name"
    assert outline[-1].endswith("members[0].skills[0].level") and outline[-1].startswith(".company.departments[1]")


def test_depth_limits_print_paths_flatten_and_keys(org):
    assert out("jq org.json -d 1 -c", org) == '{"company":"{…2 keys}"}\n'
    assert out("jq org.json -d 2 -c", org) == '{"company":{"name":"Acme","departments":"[…2 items]"}}\n'
    assert out("jq org.json -f .company.departments[0] -d 1 -c", org) == '{"name":"eng","teams":"[…2 items]"}\n'
    assert out("jq org.json -p -d 3", org).splitlines() == [
        '.company.name = "Acme"', ".company.departments[0] = {…2 keys}", ".company.departments[1] = {…2 keys}"]
    assert out("jq org.json -F -d 2 -f .company", org).splitlines() == [
        "name = Acme", "departments[0] = {…2 keys}", "departments[1] = {…2 keys}"]
    assert out("jq org.json -k -d 2", org).splitlines() == ["company", "company.name", "company.departments"]
    deeper = out("jq org.json -k -d 5", org).splitlines()
    assert deeper[3] == "company.departments[*].name" and deeper[-1] == "company.departments[*].teams[*].members[*].manager"
    assert "company.departments[*].teams[*].members[*].skills" in deeper and len(deeper) == 10
    # a list of objects: the keys of its elements; a scalar has none
    assert out("jq list.json -k -d 1", org).splitlines() == ["[*].name", "[*].age", "[*].tags"]
    with pytest.raises(PipelineError, match="no keys to outline"):
        run_line("echo 5 | jq -k -d 1", org)
    with pytest.raises(PipelineError, match="needs a number from 1"):
        run_line("jq org.json -d 0", org)


def test_flatten_lines_and_a_flat_table(org, tmp_path):
    lines = out("jq org.json -F -f .company.departments[0].teams[0]", org).splitlines()
    assert lines[:3] == ["name = core", "members[0].name = ada", "members[0].skills[0].name = py"]
    assert "members[1].skills = []" in lines and len(lines) == 8
    assert out("jq org.json -F | grep -c skills", org) == "11\n"  # flat lines are made for grep
    assert out("jq org.json -R level -F", org).splitlines()[0] == (
        "company.departments[0].teams[0].members[0].skills[0].level = 5")  # many results: paths from the root
    assert out("echo 5 | jq -F", org) == "value = 5\n"
    table = out("jq org.json -F -t -f .company.departments", org).splitlines()
    header = table[0].split("\t")
    assert header[:3] == ["name", "teams[0].name", "teams[0].members[0].name"]
    assert "teams[0].members[0].skills[0].level" in header and "teams[0].members[0].manager.name" in header
    assert table[1].split("\t")[:3] == ["eng", "core", "ada"] and table[2].split("\t")[:3] == ["ops", "sre", "di"]
    assert len(table) == 3 and all(len(row.split("\t")) == len(header) for row in table)
    # a single object: key / value rows; very wide records: the columns are capped
    assert out("jq org.json -F -t -f .company.departments[1].teams[0].members[0].manager", org).splitlines()[:3] == [
        "key\tvalue", "name\teve", "skills[0].name\tsh"]
    (tmp_path / "wide.json").write_text(json.dumps([{f"k{i:02d}": i for i in range(50)}]))
    wide = out("jq wide.json -F -t", org).splitlines()[0].split("\t")
    assert len(wide) == 41 and wide[-1] == "… +10 more" and wide[0] == "k00"


def test_failure_messages_say_what_is_there_and_what_to_type(org):
    def error(line):
        with pytest.raises(PipelineError) as caught:
            run_line(line, org)
        return str(caught.value)

    key_on_list = error("jq org.json -f .company.departments.name")
    assert "'name' on a list of 2 objects{name,teams}" in key_on_list
    assert "'name' is inside each element; try .company.departments[*].name" in key_on_list
    assert key_on_list.endswith("(at .company.departments)")
    assert "try [*].name or -i '*' -f name" in error("jq list.json -f name")  # at the root: both forms
    deeper = error("jq org.json -f .company.departments.level")
    assert "the elements have no 'level' at this level; first deeper at " in deeper and "try -R level" in deeper
    missing = error("jq org.json -f .company.departments[0].teams[0].membres")
    assert "no key 'membres' (keys: name, members); did you mean 'members'?" in missing
    below = error("jq org.json -f .company.departments[0].level")
    assert "no key 'level' (keys: name, teams); 'level' is deeper, first at .company.departments[0].teams[0]" in below
    assert "try -R level to find it at any depth" in below
    assert "[0] on an object with keys name, items, ok: an object is read by key, not by position; try .name" in (
        error("jq obj.json -f [0]"))
    assert "index 5 out of range (a list of 2): use 0..1 or -2..-1 (at .company.departments)" in (
        error("jq org.json -f .company.departments[5]"))
    assert "'x' on a string (\"Acme\"): nothing below this level (at .company.name)" in (
        error("jq org.json -f .company.name.x"))
    assert "[*] on a string (\"Acme\")" in error("jq org.json -f .company.name[*]")
    assert "a slice ([a:b]) needs a list, but this is object (at .company)" in error("jq org.json -f .company[0:2]")
    assert "bad filter" in error("jq org.json -f '.company!!'")


def test_the_header_on_screen_for_deep_data_and_none_in_pipes(org):
    org.interactive = True
    org.ui = UI(all_themes({})["nord"], Console(file=io.StringIO(), width=300))
    shown = run_line("jq org.json", org).output
    assert isinstance(shown, Styled) and json.loads(to_text(shown)) == ORG  # the plain side: JSON only
    org.ui.console.print(shown.renderable)
    screen = org.ui.console.file.getvalue().splitlines()
    assert screen[0].startswith("# object{company} -> company: object{name,departments} -> departments:")
    assert screen[1] == "# jq -k · jq -f company · jq -F · jq -R name · jq -k -d 4"  # the switches that fit
    assert out("jq org.json | cat", org).startswith("{\n")
    # a flat document keeps the one-line header
    org.ui.console.file = io.StringIO()
    org.ui.console.print(run_line("jq list.json", org).output.renderable)
    assert org.ui.console.file.getvalue().splitlines()[0].startswith("# list of 3 objects (keys: name, age, tags) · ")
    # -p / -F / -P on screen: the same text, the path in color
    for line in ("jq org.json -p -d 3", "jq org.json -F -d 3", "jq org.json -R level -P"):
        result = run_line(line, org).output
        assert isinstance(result, Styled) and " = " in to_text(result)
    assert isinstance(run_line("jq org.json -F -t -f .company.departments", org).output, Styled)


def test_deep_and_large_documents_do_not_hit_the_recursion_limit(org, tmp_path, monkeypatch):
    from shellcraft.commands.jq import _as_text, describe, key_paths, leaf_paths, limit_depth, nesting, walk
    from shellcraft.modkit import ModuleError

    deep: object = 1
    for _ in range(5000):
        deep = {"a": [deep]}  # 10000 containers: far beyond Python's recursion limit
    assert nesting(deep) == 10_000
    lines = describe(deep)
    assert lines[0].endswith("-> …") and lines[1].endswith("(10000 levels)")
    assert leaf_paths(deep)[0].endswith("[0] = 1") and len(leaf_paths(deep)) == 1
    assert len(leaf_paths(deep, depth=3)) == 1 and "[…1 item]" in leaf_paths(deep, depth=3)[0]
    limited = limit_depth(deep, 2)
    assert limited == {"a": ["{…1 key}"]}
    assert key_paths(deep, 3) == ["a", "a[*].a", "a[*].a[*].a"]
    problems: list[str] = []
    found = walk([("", deep)], [parse_path("..a")[0]], problems)
    assert len(found) == 5000 and not problems
    def too_deep(*args, **kwargs):
        raise RecursionError

    with monkeypatch.context() as patched:  # the encoder giving up: a clear message
        patched.setattr("shellcraft.commands.jq.json.dumps", too_deep)
        with pytest.raises(ModuleError, match="nested too deeply to print"):
            _as_text(deep, False)
    # through the command, as text a parser can still read
    (tmp_path / "nest.json").write_text('{"a":[' * 150 + "7" + "]}" * 150)
    assert out("jq nest.json -R a -n", org) == "150\n" and out("jq nest.json -F", org).endswith("[0] = 7\n")
    assert out("jq nest.json -s", org).splitlines()[1].endswith("(300 levels)")
    assert out("jq nest.json -p -d 2", org).splitlines() == [".a[0] = {…1 key}"]
    # JSON the parser itself cannot take is a clear message, not a traceback
    (tmp_path / "abyss.json").write_text("[" * 600_000 + "]" * 600_000)
    with pytest.raises(PipelineError, match="nested too deeply for the JSON parser"):
        run_line("jq abyss.json", org)
    # many values: 5000 records, three levels each
    records = [{"id": i, "team": {"name": f"t{i % 7}", "members": [{"name": f"m{i}", "tags": ["x", "y"]}]}}
               for i in range(5000)]
    (tmp_path / "big.json").write_text(json.dumps(records))
    assert out("jq big.json -R tags -n", org) == "5000\n"
    assert out("jq big.json -W 'name=m4999' -f tags[*]", org) == "x\ny\n"
    assert out("jq big.json -F | wc -l", org).strip() == str(5000 * 5)
    assert out("jq big.json -s", org).splitlines()[0].startswith("list of 5000 objects{id,team} -> team: object{name,members}")


def test_the_jq_line_is_not_globbed_for_the_new_switches(org):
    words = [t.value for t in tokenize("jq -f [*].items[*].tags[*] -R name ..id", glob=True) if not t.op]
    assert words == ["jq", "-f", "[*].items[*].tags[*]", "-R", "name", "..id"]


def test_small_cases_of_the_new_paths(org):
    assert out("jq obj.json .", org) == out("jq obj.json", org)  # a lone dot is the whole document, not a file
    assert out("jq list.json -f '[0:2]' -P -c", org).startswith('[0:2] = [{"name":"ada"')
    assert out("jq list.json -R name -P", org) == "[0].name = ada\n[1].name = bob\n"
    assert out('jq list.json -R tags -F', org) == "[0].tags[0] = x\n"  # many results: paths from the root
    # numbers compare with numbers: "ada" is not greater than 3, "12" (text) is
    assert out("jq list.json -W '>3' -P", org) == "[0].age = 36\n[2].age = 5\n"
    assert out("jq list.json -W '>3'", org) == "36\n5\n"
    assert out("jq list.json -W '>10'", org) == "36\n"
    with pytest.raises(PipelineError, match="one FILE at a time|no such file|cannot read"):
        run_line("jq nosuch.json ..x", org)
