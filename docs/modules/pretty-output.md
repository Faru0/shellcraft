# Tables and lists from your module

← [Manual](../README.md)

This page is for you if your module has rows or records to show and you don't want to line up
columns by hand. After reading it you'll know the two functions that do it, what each shape of
data gives you, how the colours work, and what a pipe or an AI client gets instead.

There is no new command at the prompt. You import two functions in your module's code:

```python
from shellcraft import table, listing


def run(args, stdin):
    rows = [{"host": "web01", "status": "ok", "load": 0.4},
            {"host": "db01", "status": "down", "load": 9.1},
            {"host": "cache01", "status": "degraded", "load": 2.2}]
    return table(rows, title="Hosts")
```

Save that as `~/.shellcraft/modules/hosts.py` and type `hosts`:

```text
            Hosts
┏━━━━━━━━━┳━━━━━━━━━━┳━━━━━━┓
┃ host    ┃ status   ┃ load ┃
┡━━━━━━━━━╇━━━━━━━━━━╇━━━━━━┩
│ web01   │ ok       │  0.4 │
│ db01    │ down     │  9.1 │
│ cache01 │ degraded │  2.2 │
└─────────┴──────────┴──────┘
```

On your screen `ok` is green, `degraded` is yellow and `down` is red, in the colours of your theme.

You return the result, as you return text. Where it goes is not your module's business:

| Where the output goes | What it gets |
| --- | --- |
| Your screen, as the last command | The pretty table or list, in colour |
| A pipe: `hosts \| grep down` | Plain text, tabs between the fields |
| A file: `hosts > hosts.txt` | The same plain text |
| A variable: `x=$(hosts)` | The same plain text |
| An AI client over MCP | The same plain text |

So `hosts | cut -f 1` gives the host names, and nothing in a pipe has colour codes or box lines in it.

The same two names are in `shellcraft.modkit`, if you already import from there:
`from shellcraft.modkit import ArgParser, table, listing`.

## A table

```python
table(data, columns=None, title=None, colors=None, style="boxed", plain="tsv")
```

What you pass as `data` decides the rows:

| You pass | You get |
| --- | --- |
| A list of dicts | One row for each dict. The columns are every key seen, in the order they first appear. |
| A list of lists (or tuples) | One row each. Give the header with `columns=["host", "port"]`. |
| One dict | Two columns, `key` and `value`, one row for each key. |
| A list of plain values | One column, `value`. |
| JSON text | Read first, then one of the above. Handy for an API answer: `table(reply.text)`. |

`columns` picks the columns and their order. A dict also renames them:

```python
table(rows, columns=["status", "host"])                    # two columns, in this order
table(rows, columns={"host": "Host", "status": "State"})   # the same pick, with your own header
```

`title` is a line above the table. It is for the screen only, a pipe does not get it.

`style` is the look. `"boxed"` is the default and looks like `fleet`, `env` and `settings`. The other
two are lighter:

```text
style="lines"                    style="bare"

Hosts                            Hosts
host      status     load        host      status     load
─────────────────────────        web01     ok          0.4
web01     ok          0.4        db01      down        9.1
db01      down        9.1        cache01   degraded    2.2
cache01   degraded    2.2
```

A number column is lined up to the right. A cell that is too long for the screen folds onto the
next line inside its column. In a pipe nothing is folded or cut.

## A list

```python
listing(data, tag=None, title=None, fields=None, colors=None, plain="tsv")
```

A list has three looks, and the data picks the one that fits.

**One dict** is one record, a line for each field:

```python
listing({"host": "web01", "status": "ok", "ports": [22, 443], "backup": True, "owner": None},
        title="web01")
```

```text
web01
  host    web01
  status  ok
  ports   22, 443
  backup  yes
  owner   -
```

**A list of dicts** is one line for each item, with a coloured dot and tag in front:

```python
found = [{"severity": "critical", "host": "db01", "text": "disk full on /var"},
         {"severity": "high", "host": "web02", "text": "certificate expires in 3 days"},
         {"severity": "low", "host": "cache01", "text": "2 packages can be updated"}]
listing(found, title="Findings")
```

```text
Findings
  ● critical  db01     disk full on /var
  ● high      web02    certificate expires in 3 days
  ● low       cache01  2 packages can be updated
```

The tag is the field that gives the line its colour. You name it with `tag="severity"`. If you
leave it out, it is the first of `severity`, `status`, `state`, `level`, `result` and `verdict`
that your items have. If they have none of these, each line starts with a plain `•`. Use
`tag=False` when your items have such a field and you don't want a tag.

**A list of plain values** is bullets:

```python
listing(["core", "ops", "sec"], title="Teams")
```

```text
Teams
  • core
  • ops
  • sec
```

`fields` works as `columns` does for a table: it picks the fields, sets their order, and a dict
renames them (the names show for one record).

## How values are shown

| Value | Shown as |
| --- | --- |
| `True`, `False` | `yes`, `no` |
| `None`, `""`, a key the row does not have | a grey `-` |
| A list | `a, b, c` |
| A dict inside a row | one line of JSON |
| Anything else | as `str()` gives it |

## Colours

Some words colour themselves. A cell is coloured only when it is exactly one of these words, in
upper or lower case:

| Colour | Words |
| --- | --- |
| Green | `ok` `up` `yes` `true` `pass` `passed` `open` |
| Yellow | `warn` `warning` `medium` `degraded` |
| Red | `fail` `failed` `down` `error` `no` `false` `high` |
| Red and bold | `crit` `critical` |
| Grey | `info` `low`, and an empty value |

The colours are the ones of your theme, so they change when you change the theme.

When your data uses other words, say what they mean, column by column:

```python
table(rows, colors={"state": {"running": "green", "stopped": "red", "paused": "yellow"}})
```

For numbers, give a function. It gets the value and returns a colour, or `None` for no colour:

```python
table(rows, colors={"load": lambda value: "red" if value > 8 else None})
```

The colour names are `green`, `yellow`, `red`, `grey`, `accent` and `plain` (no colour). These
follow your theme. You can also give any Rich style, such as `"bold cyan"`.

A word your dict does not list keeps its automatic colour. To turn the colours off, pass
`colors=False` for everything, or `colors={"status": False}` for one column.

In a list, the dot takes the colour of its tag. A tag word that has no colour gets the accent
colour.

## What a pipe gets

By default a pipe, a file or an AI client gets tab-separated text: for a table a header line and
then one line for each row, for a list one line for each item with no header.

```text
❯ hosts | grep -v ok
host    status  load
db01    down    9.1
cache01 degraded        2.2
❯ hosts | cut -f 1,2 | grep down
db01    down
```

A tab or a line break inside a value becomes a space, so one row is always one line and one value
is always one field.

Your module can ask for another form with `plain=`:

| `plain=` | A pipe gets |
| --- | --- |
| `"tsv"` | Tabs between the fields. The default. |
| `"columns"` | The fields lined up with spaces, easy to read in a file. |
| `"json"` | The data itself as JSON, so `hosts \| jq -w status=down` works. |

A common way is to let the person who runs the command choose:

```python
parser.add_argument("--json", action="store_true")
...
return table(rows, plain="json" if opts.json else "tsv")
```

## More than one in a result

Join two results with `+`. Text joins too:

```python
return table(rows, title="Hosts") + "\n" + listing(found, title="Findings")
```

```text
            Hosts
┏━━━━━━━━━┳━━━━━━━━━━┳━━━━━━┓
┃ host    ┃ status   ┃ load ┃
┡━━━━━━━━━╇━━━━━━━━━━╇━━━━━━┩
│ web01   │ ok       │  0.4 │
│ db01    │ down     │  9.1 │
│ cache01 │ degraded │  2.2 │
└─────────┴──────────┴──────┘

Findings
  ● critical  db01     disk full on /var
  ● high      web02    certificate expires in 3 days
  ● low       cache01  2 packages can be updated
```

## When there is nothing to show

An empty list or dict gives no output at all, on screen and in a pipe. If you'd rather say so,
check first:

```python
if not rows:
    return "no hosts\n"
return table(rows)
```

## When the data is wrong

Both functions raise `ModuleError` with one clear line, which the shell shows as your module's
error:

```text
table: give it a list, a dict or JSON text, not int
table: the text is not JSON (Expecting value: line 1 column 1 (char 0))
table: a list of lists needs columns=[...] for the header
table: 'blu' is not a colour (use green, yellow, red, grey, accent, or a Rich style such as 'bold cyan')
listing: plain is 'tsv', 'columns' or 'json', not 'csv'
```

## A full module to copy

It takes host names as arguments or piped in, and has a `--list` and a `--json` switch.

```python
"""hosts: the state of your hosts, as a table or a list."""

from shellcraft import listing, table
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "The state of your hosts"

HOSTS = [{"host": "web01", "status": "ok", "load": 0.4, "ports": [22, 443]},
         {"host": "db01", "status": "down", "load": 9.1, "ports": [22, 5432]},
         {"host": "cache01", "status": "degraded", "load": 2.2, "ports": [22, 6379]}]


def run(args, stdin):
    parser = ArgParser("hosts")
    parser.add_argument("names", nargs="*", help="only these hosts")
    parser.add_argument("-l", "--list", action="store_true", help="one line for each host")
    parser.add_argument("--json", action="store_true", help="JSON in a pipe")
    opts = parser.parse_args(args)

    names = opts.names or stdin.split()
    rows = [row for row in HOSTS if not names or row["host"] in names]
    if not rows:
        raise ModuleError("hosts: no such host")
    plain = "json" if opts.json else "tsv"
    hot = {"load": lambda value: "red" if value > 8 else None}
    if opts.list:
        return listing(rows, tag="status", title="Hosts", colors=hot, plain=plain)
    return table(rows, title="Hosts", colors=hot, plain=plain)
```

```text
❯ hosts --list
Hosts
  ● ok        web01    0.4  22, 443
  ● down      db01     9.1  22, 5432
  ● degraded  cache01  2.2  22, 6379
❯ hosts --json | jq -w status=down -f host
db01
```

The checker takes it as it takes text: `ingest check ~/.shellcraft/modules/hosts.py` says
"returns a table or a list".

## Next steps

- [Your first module](first-module.md): the rest of a module, step by step.
- [Module reference](reference.md): every rule, file and option in one place.
