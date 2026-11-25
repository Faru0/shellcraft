"""shellcraft.pretty: `table` and `listing`, what a module returns for a pretty table or list."""

import io
import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.errors import MissingStyle
from rich.style import Style

import shellcraft
from shellcraft import listing, modkit, pretty, rounds, table
from shellcraft.commands import findings as findings_cmd
from shellcraft.commands import fleet as fleet_cmd
from shellcraft.context import ShellContext, Styled, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.modkit import ModuleError
from shellcraft.pipeline import run_line
from shellcraft.themes import all_themes
from tests.conftest import ROOT
from tests.test_ingest import FAIL, check_module
from tests.test_mcp import _call, build_server

HOSTS = [{"host": "web01", "status": "ok", "load": 0.4, "ports": [22, 80], "backup": True, "owner": "ada"},
         {"host": "db01", "status": "down", "load": 9.1, "ports": [22, 5432], "backup": False, "owner": None}]
FOUND = [{"severity": "critical", "host": "db01", "text": "disk full on /var"},
         {"severity": "high", "host": "web02", "text": "certificate expires in 3 days"},
         {"severity": "low", "host": "cache01", "text": "2 packages can be updated"}]


def console(theme="cyberpunk", width=120):
    return Console(file=io.StringIO(), width=width, theme=all_themes({})[theme].rich_theme())


def screen(value, theme="cyberpunk", width=120):
    """The screen face, as text (without the spaces that fill a line up to its column)."""
    con = console(theme, width)
    con.print(value.renderable)
    return "".join(f"{line.rstrip()}\n" for line in con.file.getvalue().splitlines())


def looks(value, theme="cyberpunk"):
    """The style every piece of text has on screen: {text: Style}."""
    con = console(theme)
    return {seg.text.strip(): seg.style or Style() for seg in con.render(value.renderable) if seg.text.strip()}


def look(name, theme="cyberpunk"):
    return console(theme).get_style(name)


def same(style, name, theme="cyberpunk"):
    wanted = look(name, theme)
    return style.color == wanted.color and bool(style.bold) == bool(wanted.bold)


# ── table: the shapes of data ────────────────────────────────────────────────

def test_a_list_of_dicts_is_one_row_each():
    result = table(HOSTS)
    assert result.text == ("host\tstatus\tload\tports\tbackup\towner\n"
                           "web01\tok\t0.4\t22, 80\tyes\tada\n"
                           "db01\tdown\t9.1\t22, 5432\tno\t-\n")
    shown = screen(result)
    assert "┃ host  ┃ status ┃ load ┃" in shown and "│ db01  │ down   │  9.1 │" in shown  # numbers to the right


def test_the_columns_are_every_key_seen_in_order():
    assert table([{"a": 1}, {"b": 2, "a": 3}]).text == "a\tb\n1\t-\n3\t2\n"


def test_columns_picks_orders_and_renames():
    assert table(HOSTS, columns=["status", "host"]).text == "status\thost\nok\tweb01\ndown\tdb01\n"
    assert table(HOSTS, columns={"host": "Host", "status": "State"}).text == "Host\tState\nweb01\tok\ndb01\tdown\n"
    assert table(HOSTS, columns=["host", "nope"]).text == "host\tnope\nweb01\t-\ndb01\t-\n"


def test_a_list_of_lists_needs_its_columns():
    assert table([["a", 1], ("b",)], columns=["name", "n"]).text == "name\tn\na\t1\nb\t-\n"
    with pytest.raises(ModuleError, match=r"table: a list of lists needs columns=\[\.\.\.\]"):
        table([["a", 1]])
    with pytest.raises(ModuleError, match="table: row 2 has 3 values for 2 columns"):
        table([["a", 1], ["b", 2, 3]], columns=["name", "n"])


def test_one_dict_is_key_and_value_rows():
    assert table({"host": "web01", "up": True}).text == "key\tvalue\nhost\tweb01\nup\tyes\n"
    assert table({"host": "web01"}, columns=["Setting", "Now"]).text == "Setting\tNow\nhost\tweb01\n"
    with pytest.raises(ModuleError, match="for one dict, columns is the two header names"):
        table({"host": "web01"}, columns=["a"])


def test_plain_values_are_one_column():
    assert table(["a", 2, None]).text == "value\na\n2\n-\n"
    assert table(["a"], columns=["name"]).text == "name\na\n"
    assert table(x for x in ("a", "b")).text == "value\na\nb\n"


def test_json_text_is_read():
    assert table('[{"a": 1, "b": [1, 2]}]').text == "a\tb\n1\t1, 2\n"
    assert table(b'{"a": null}').text == "key\tvalue\na\t-\n"
    assert table("  \n").text == ""
    with pytest.raises(ModuleError, match=r"table: the text is not JSON \("):
        table("host status")
    with pytest.raises(ModuleError, match="table: give it a list, a dict or JSON text, not int"):
        table("5")
    with pytest.raises(ModuleError, match="not NoneType"):
        table(None)


def test_nothing_gives_nothing():
    assert table([]).text == "" and listing([]).text == "" and listing({}).text == ""
    assert table([], columns=["host", "status"]).text == "host\tstatus\n"


def test_how_values_are_shown():
    row = {"yes": True, "no": False, "none": None, "empty": "", "list": ["a", 1, None], "nolist": [],
           "dict": {"k": [1], "ü": "é"}, "set": {"b", "a"}, "float": 2.50, "zero": 0}
    assert table([row]).text.splitlines()[1].split("\t") == [
        "yes", "no", "-", "-", "a, 1, -", "-", '{"k": [1], "ü": "é"}', "a, b", "2.5", "0"]


def test_a_value_stays_one_field_in_a_pipe():
    result = table([{"note": "one\ttwo\nthree\r\nfour", "n": 1}])
    assert result.text == "note\tn\none two three four\t1\n"
    assert "three" in screen(result)


# ── table: looks and plain forms ─────────────────────────────────────────────

def test_the_three_looks():
    boxed, lines, bare = (screen(table(HOSTS, title="Hosts", style=s)) for s in ("boxed", "lines", "bare"))
    assert "┏" in boxed and "│" in boxed and "Hosts" in boxed
    assert "─" in lines and "│" not in lines and lines.startswith("Hosts")
    assert not set("─│┏┃") & set(bare) and bare.startswith("Hosts") and "web01" in bare
    assert "Hosts" not in table(HOSTS, title="Hosts").text  # a title is for the screen
    with pytest.raises(ModuleError, match="table: style is 'boxed', 'lines' or 'bare', not 'fancy'"):
        table(HOSTS, style="fancy")


def test_a_long_cell_folds_on_a_narrow_screen():
    result = table([{"host": "web01", "note": "the front door, behind the balancer"}])
    shown = screen(result, width=30)
    assert all(len(line) <= 30 for line in shown.splitlines()) and "balancer" in shown.replace("\n", "")
    assert "the front door, behind the balancer" in result.text  # nothing is cut in a pipe


def test_plain_columns_are_lined_up():
    assert table(HOSTS, columns=["host", "status", "owner"], plain="columns").text == ("host   status  owner\n"
                                                                                      "web01  ok      ada\n"
                                                                                      "db01   down    -\n")
    assert listing(FOUND, plain="columns").text.splitlines()[1] == "high      web02    certificate expires in 3 days"


def test_plain_json_is_the_data_itself():
    assert json.loads(table(HOSTS, plain="json").text) == HOSTS
    assert json.loads(table(HOSTS, columns={"host": "Host", "load": "Load"}, plain="json").text) == [
        {"host": "web01", "load": 0.4}, {"host": "db01", "load": 9.1}]
    assert json.loads(table([["a", 1]], columns=["name", "n"], plain="json").text) == [{"name": "a", "n": 1}]
    assert json.loads(table({"a": 1}, plain="json").text) == {"a": 1}
    assert json.loads(listing(FOUND, fields=["host"], plain="json").text)[0] == {"severity": "critical", "host": "db01"}
    assert json.loads(listing(["a", 1], plain="json").text) == ["a", 1]
    for make in (table, listing):
        with pytest.raises(ModuleError, match=rf"{make.__name__}: plain is 'tsv', 'columns' or 'json', not 'csv'"):
            make(HOSTS, plain="csv")


# ── colours ──────────────────────────────────────────────────────────────────

def test_known_words_colour_themselves():
    rows = [{"a": "ok", "b": "degraded", "c": "down", "d": "critical", "e": "low", "f": None, "g": "OK ", "h": "okay",
             "i": True, "j": False, "k": "high", "l": "web01"}]
    found = looks(table(rows))
    assert same(found["ok"], "sc.success") and same(found["OK"], "sc.success") and same(found["yes"], "sc.success")
    assert same(found["degraded"], "sc.warning")
    assert same(found["down"], "sc.danger") and same(found["no"], "sc.danger") and same(found["high"], "sc.danger")
    assert same(found["critical"], "sc.error") and found["critical"].bold and not found["high"].bold
    assert found["critical"].color == found["high"].color  # the same red, only the weight differs
    assert same(found["low"], "sc.muted") and same(found["-"], "sc.muted")
    assert found["okay"].color is None and found["web01"].color is None  # only the exact word


@pytest.mark.parametrize("theme", ["cyberpunk", "nord", "solarized-light"])
def test_the_colours_follow_the_theme(theme):
    found = looks(table(HOSTS), theme)
    assert same(found["ok"], "sc.success", theme) and same(found["down"], "sc.danger", theme)
    assert same(found["host"], "sc.accent", theme)


def test_your_own_colours_for_a_column():
    rows = [{"name": "a", "state": "running"}, {"name": "b", "state": "Stopped"}, {"name": "c", "state": "ok"},
            {"name": "d", "state": True}]
    found = looks(table(rows, colors={"state": {"running": "green", "stopped": "red", True: "accent", "x": "grey"}}))
    assert same(found["running"], "sc.success") and same(found["Stopped"], "sc.danger")
    assert same(found["ok"], "sc.success")  # not in the dict: the automatic colour
    assert same(found["yes"], "sc.accent")
    plain = looks(table(rows, colors={"state": {"ok": "plain"}}))
    assert plain["ok"].color is None


def test_a_function_colours_by_the_value():
    seen = []

    def hot(value):
        seen.append(value)
        return "red" if value > 8 else None

    found = looks(table([*HOSTS, {"host": "new01"}, {"host": "odd", "load": "n/a"}], colors={"load": hot}))
    assert same(found["9.1"], "sc.danger") and found["0.4"].color is None
    assert 9.1 in seen and 0.4 in seen  # the value itself, not its text
    with pytest.raises(ModuleError, match="table: 'blu' is not a colour"):
        table(HOSTS, colors={"load": lambda value: "blu"})


def test_rich_styles_and_theme_names_are_colours_too():
    found = looks(table(HOSTS, colors={"host": {"web01": "bold cyan", "db01": Style(color="magenta")},
                                       "status": {"ok": "sc.path"}}))
    assert found["web01"].bold and found["web01"].color.name == "cyan" and found["db01"].color.name == "magenta"
    assert same(found["ok"], "sc.path")


def test_colours_can_be_turned_off():
    found = looks(table(HOSTS, colors=False))
    assert found["ok"].color is None and found["down"].color is None and same(found["-"], "sc.muted")
    one = looks(table(HOSTS, colors={"status": False}))
    assert one["ok"].color is None and same(one["yes"], "sc.success")


@pytest.mark.parametrize("colors, message", [
    ("red", "table: colors is a dict, one entry for a column"),
    ({"status": "red"}, r"table: colors\['status'\] is a dict of value and colour, a function, or False, not str"),
    ({"status": {"ok": "blu"}}, "table: 'blu' is not a colour"),
    ({"status": {"ok": 5}}, "table: 5 is not a colour"),
])
def test_colours_that_make_no_sense(colors, message):
    with pytest.raises(ModuleError, match=message):
        table(HOSTS, colors=colors)


def test_one_dict_is_coloured_by_its_keys():
    found = looks(table({"status": "ok", "load": 9.1}, colors={"load": lambda value: "red" if value > 8 else None}))
    assert same(found["ok"], "sc.success") and same(found["9.1"], "sc.danger")


# ── listing ──────────────────────────────────────────────────────────────────

def test_one_dict_lists_its_fields():
    result = listing(HOSTS[1], title="The database")
    assert result.text == "host\tdb01\nstatus\tdown\nload\t9.1\nports\t22, 5432\nbackup\tno\nowner\t-\n"
    shown = screen(result).splitlines()
    assert shown[0] == "The database" and shown[1] == "  host    db01" and shown[2] == "  status  down"
    found = looks(result)
    assert same(found["status"], "sc.accent") and same(found["down"], "sc.danger")
    assert same(found["The database"], "sc.prompt") and found["db01"].color is None


def test_fields_picks_orders_and_renames():
    assert listing(HOSTS[0], fields=["status", "host", "nope"]).text == "status\tok\nhost\tweb01\nnope\t-\n"
    assert listing(HOSTS[0], fields={"host": "Name", "load": "Load"}).text == "Name\tweb01\nLoad\t0.4\n"
    assert listing(FOUND, fields=["text", "host"]).text.splitlines()[0] == "critical\tdisk full on /var\tdb01"
    with pytest.raises(ModuleError, match="listing: fields is a list of names, or a dict to rename them, not str"):
        listing(HOSTS[0], fields="host")
    with pytest.raises(ModuleError, match="table: columns is a list of names"):
        table(HOSTS, columns="host")


def test_a_list_of_dicts_is_one_line_each_with_a_coloured_tag():
    result = listing(FOUND, title="Findings")
    assert result.text == ("critical\tdb01\tdisk full on /var\n"
                           "high\tweb02\tcertificate expires in 3 days\n"
                           "low\tcache01\t2 packages can be updated\n")
    shown = screen(result).splitlines()
    assert shown[0] == "Findings" and shown[1] == "  ● critical  db01     disk full on /var"
    assert shown[2] == "  ● high      web02    certificate expires in 3 days"
    found = looks(result)
    assert same(found["critical"], "sc.error") and same(found["high"], "sc.danger") and same(found["low"], "sc.muted")
    assert same(found["db01"], "sc.path") and found["disk full on /var"].color is None


def test_the_dot_has_the_colour_of_its_tag():
    dots = [seg.style for seg in console().render(listing(FOUND).renderable) if seg.text == "● "]
    assert [same(dot, name) for dot, name in zip(dots, ("sc.error", "sc.danger", "sc.muted"))] == [True] * 3
    found = looks(listing([{"state": "pending", "name": "job"}, {"name": "other"}]))
    assert same(found["pending"], "sc.accent") and same(found["-"], "sc.muted")  # an unknown word, and no tag at all
    own = looks(listing([{"state": "pending", "name": "job"}], colors={"state": {"pending": "yellow"}}))
    assert same(own["pending"], "sc.warning") and same(own["●"], "sc.warning")


def test_which_field_is_the_tag():
    items = [{"name": "a", "status": "ok", "severity": "high", "kind": "disk"}]
    assert listing(items).text == "high\ta\tok\tdisk\n"  # severity comes before status
    assert listing([{"name": "a", "Status": "ok"}]).text == "ok\ta\n"
    assert listing(items, tag="kind").text == "disk\ta\tok\thigh\n"
    untagged = listing(items, tag=False)
    assert untagged.text == "a\tok\thigh\tdisk\n" and screen(untagged) == "  •  a  ok  high  disk\n"
    assert listing([{"name": "a", "size": 3}]).text == "a\t3\n"  # no field to tag by: a plain bullet
    assert listing(items, tag="nope").text == "-\ta\tok\thigh\tdisk\n"


def test_plain_values_are_bullets():
    result = listing(["core", "ops", 3, None, {"a": 1}], title="Teams")
    assert result.text == 'core\nops\n3\n-\n{"a": 1}\n'
    assert screen(result) == 'Teams\n  • core\n  • ops\n  • 3\n  • -\n  • {"a": 1}\n'
    assert same(looks(result)["•"], "sc.accent")
    assert listing('["a", "b"]').text == "a\nb\n"


def test_a_long_line_folds_under_its_own_column():
    shown = screen(listing([{"status": "ok", "host": "web01", "text": "the front door, behind the balancer"}]),
                   width=34).splitlines()
    assert shown[0].startswith("  ● ok  web01  the front") and shown[1].startswith(" " * 15) and len(shown) > 1


# ── joining ──────────────────────────────────────────────────────────────────

def test_results_join_with_plus():
    both = table(HOSTS, columns=["host"]) + "\n" + listing(["a"])
    assert isinstance(both, pretty.Pretty) and both.text == "host\nweb01\ndb01\n\na\n"
    shown = screen(both).splitlines()
    assert shown[0].startswith("┏") and shown[-2] == "" and shown[-1] == "  • a"
    headed = "Hosts:" + table(["x"])
    assert headed.text == "Hosts:\nvalue\nx\n" and screen(headed).splitlines()[0] == "Hosts:"
    assert (listing(["a"]) + listing(["b"]) + "done").text == "a\nb\ndone"
    assert (listing([]) + "nothing found\n").text == "nothing found\n"
    with pytest.raises(TypeError):
        table(HOSTS) + 5


def test_a_result_is_two_faced_output():
    result = table(HOSTS)
    assert isinstance(result, Styled) and to_text(result) == result.text == str(result) == f"{result}"


# ── the names ────────────────────────────────────────────────────────────────

def test_the_names_are_in_shellcraft_and_in_modkit():
    assert shellcraft.table is modkit.table is pretty.table and shellcraft.listing is modkit.listing is pretty.listing
    for module in (shellcraft, modkit):
        with pytest.raises(AttributeError, match="has no attribute 'tables'"):
            _ = module.tables


def test_import_shellcraft_stays_light():
    code = ("import sys, shellcraft; assert shellcraft.__version__; "
            "assert 'rich' not in sys.modules and 'shellcraft.pretty' not in sys.modules, sorted(sys.modules)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr


# ── a module that returns them ───────────────────────────────────────────────

MODULE = '''
    """hosts: a module of the test."""
    from shellcraft import listing, table

    HOSTS = [{"host": "web01", "status": "ok", "load": 0.4}, {"host": "db01", "status": "down", "load": 9.1}]


    def run(args, stdin):
        if args == ["list"]:
            return listing(HOSTS, title="Hosts")
        if args == ["both"]:
            return table(HOSTS) + "\\n" + listing(HOSTS[0])
        return table(HOSTS, title="Hosts")
'''


@pytest.fixture
def mods(tmp_path, monkeypatch):
    folder = tmp_path / "mods"
    folder.mkdir()
    (folder / "hosts.py").write_text(textwrap.dedent(MODULE), encoding="utf-8")
    (folder / "hosts.skill").write_text('summary = "The hosts."\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    registry = ModuleRegistry(folder)
    registry.load()
    return registry


def test_as_the_last_command_it_is_the_pretty_one(mods):
    output = run_line("hosts", ShellContext(registry=mods)).output
    assert isinstance(output, pretty.Pretty)
    shown = screen(output)
    assert "Hosts" in shown and "│ db01  │ down   │  9.1 │" in shown
    assert same(looks(output)["down"], "sc.danger")
    assert screen(run_line("hosts list", ShellContext(registry=mods)).output).splitlines()[1] == "  ● ok    web01  0.4"
    assert "● ok" not in to_text(run_line("hosts both", ShellContext(registry=mods)).output)


def test_everywhere_else_it_is_plain_text(mods, tmp_path):
    ctx = ShellContext(registry=mods)
    assert to_text(run_line("hosts | cut -f 1,2", ctx).output) == "host\tstatus\nweb01\tok\ndb01\tdown\n"
    assert to_text(run_line("hosts | grep -c down", ctx).output) == "1\n"
    run_line("hosts > out.txt", ctx)
    assert (tmp_path / "out.txt").read_text() == "host\tstatus\tload\nweb01\tok\t0.4\ndb01\tdown\t9.1\n"
    assert to_text(run_line('x=$(hosts list); echo "$x"', ctx).output) == "ok\tweb01\t0.4\ndown\tdb01\t9.1\n"


def test_an_ai_client_gets_the_plain_text(mods):
    result = _call(build_server(mods), lambda c: c.call_tool("hosts", {"args": ["both"]}))
    assert not result.is_error
    assert result.content[0].text == ("host\tstatus\tload\nweb01\tok\t0.4\ndb01\tdown\t9.1\n\n"
                                      "host\tweb01\nstatus\tok\nload\t0.4\n")


def test_a_watch_round_reads_the_plain_text():
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(run=lambda args, stdin: listing({"plan": "every 5 min"})))
    assert rounds.interval_minutes(registry, "prod") == 5


def test_the_checker_takes_a_table_or_a_list(mods, tmp_path):
    checks = check_module(tmp_path / "mods" / "hosts.py").checks
    assert "returns a table or a list" in [c.detail for c in checks]
    assert not [c for c in checks if c.status == FAIL and c.name.startswith("run(")]
    (tmp_path / "num.py").write_text("def run(args, stdin):\n    return 5\n")
    assert any(c.status == FAIL and "run() must return str, or a table / listing from shellcraft" in c.detail
               for c in check_module(tmp_path / "num.py").checks)


# ── the builtins' own colours ────────────────────────────────────────────────

@pytest.mark.parametrize("styles", [fleet_cmd.STATUS_STYLES, fleet_cmd.SEVERITY_STYLES, findings_cmd.SEVERITY_STYLES,
                                    pretty.WORDS, pretty.COLOURS])
def test_every_colour_is_a_style_the_theme_has(styles):
    """A name such as "bold sc.error" is not one: Rich cannot resolve it and prints the text plain."""
    for theme in all_themes({}):
        for name in styles.values():
            try:
                console(theme).get_style(name)
            except MissingStyle:
                pytest.fail(f"{name!r} is not a style of the {theme} theme")


def test_critical_is_bold_and_high_is_the_same_red():
    for styles, worst in ((fleet_cmd.SEVERITY_STYLES, "critical"), (findings_cmd.SEVERITY_STYLES, "critical"),
                          (fleet_cmd.STATUS_STYLES, "crit")):
        critical = look(styles[worst])
        assert critical.bold and critical.color == look("sc.error").color
        if "high" in styles:
            assert look(styles["high"]).color == critical.color and not look(styles["high"]).bold
