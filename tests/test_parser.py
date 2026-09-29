import pytest

from core.parser import ParseError, parse, tokenize


def test_blank_line():
    assert parse("   ") is None


def test_simple_pipeline_and_redirect():
    p = parse("fetch data.csv | filter -i error > out.txt")
    assert [(c.name, c.args) for c in p.segments] == [("fetch", ["data.csv"]), ("filter", ["-i", "error"])]
    assert p.redirect.path == "out.txt" and not p.redirect.append


def test_append_and_operators_without_spaces():
    p = parse("a|b>>log.txt")
    assert [c.name for c in p.segments] == ["a", "b"]
    assert p.redirect.path == "log.txt" and p.redirect.append


def test_quotes_protect_operators_and_spaces():
    p = parse('filter "foo|bar > baz" \'it\'\'s\'')
    assert p.segments[0].args == ["foo|bar > baz", "its"]


def test_escaped_double_quote():
    assert [t.value for t in tokenize(r'echo "say \"hi\""')] == ["echo", 'say "hi"']


def test_windows_paths_keep_backslashes():
    p = parse(r"fetch C:\data\logs.txt > C:\out\x.txt")
    assert p.segments[0].args == [r"C:\data\logs.txt"]
    assert p.redirect.path == r"C:\out\x.txt"


@pytest.mark.parametrize("line, fragment", [
    ("| a", "empty pipeline segment"),
    ("a |", "ends with"),
    ("a | | b", "empty pipeline segment"),
    ("a >", "missing file name"),
    ("a > f | b", "must be the last"),
    ("a > f g", "must be the last"),
    ("> f", "missing command"),
    ('echo "oops', "unterminated"),
])
def test_parse_errors(line, fragment):
    with pytest.raises(ParseError) as info:
        parse(line)
    assert fragment in info.value.message


@pytest.mark.parametrize("line, op", [
    ("rm a.txt ; ls", ";"),
    ("a;b", ";"),
    ("a && b", "&&"),
    ("a || b", "||"),
    ("sort < in.txt", "<"),
    ("a 2> err.txt", "2>"),
    ("a 2>> err.txt", "2>>"),
    ("a 2>&1", "2>&1"),
    ("a &> out.txt", "&>"),
    ("echo x >&2", ">&"),
])
def test_unsupported_operators_are_rejected(line, op):
    with pytest.raises(ParseError) as info:
        parse(line)
    assert info.value.message.startswith(f"'{op}' is not supported")


def test_unsupported_operators_are_literal_when_quoted():
    p = parse("filter 'a;b' \"x && y\" '<tag>' '2>'")
    assert p.segments[0].args == ["a;b", "x && y", "<tag>", "2>"]


def test_two_only_redirects_stderr_at_word_start():
    p = parse("echo abc2>x")
    assert p.segments[0].args == ["abc2"] and p.redirect.path == "x"
    p = parse("echo 2 > x")
    assert p.segments[0].args == ["2"] and p.redirect.path == "x"


def test_single_ampersand_stays_in_urls():
    assert parse("fetch https://x/?a=1&b=2").segments[0].args == ["https://x/?a=1&b=2"]
