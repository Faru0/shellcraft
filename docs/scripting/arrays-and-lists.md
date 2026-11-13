# Arrays, lists and dicts

← [Scripting guide](README.md)

A plain variable holds one value. An **array** holds many, in order: the servers you check every
morning, the lines of a file, the arguments of a function. A **dict** holds values you look up by
a name instead of a number: the port of each service, the owner of each folder.

You can write both the bash way (`hosts=(web01 db01)`) or the Python way
(`hosts = ["web01", "db01"]`). The two spellings make the same thing, so you can set a list in one
style and read it in the other. After this page you'll know how to make a list, change it, loop
over it, fill it from a file or a command, and which few things work differently from bash and
from Python.

`man arrays` is the short version of this page.

## Make an array and read it

Put the values in brackets with spaces between them. Quotes keep a value with spaces together:

<!-- run -->
```
hosts=(web01 db01 "mail server")
echo "the first is ${hosts[0]}"
echo "the last is ${hosts[-1]}"
echo "there are ${#hosts[@]}"
```

<!-- output -->
```
the first is web01
the last is mail server
there are 3
```

Counting starts at 0, so `${hosts[0]}` is the first element and `${hosts[1]}` the second. A
negative number counts from the end: `${hosts[-1]}` is the last one. The curly brackets are needed:
`$hosts[0]` is the first element followed by the text `[0]`.

There are no spaces around the `=`, as with any bash variable. A long list can go over several
lines:

<!-- run -->
```
folders=(
  /etc
  /var/log
  "/home/alice/My Documents"
)
echo "${#folders[@]} folders, the third is ${folders[2]}"
```

<!-- output -->
```
3 folders, the third is /home/alice/My Documents
```

| You write | You get |
| --- | --- |
| `${a[N]}` | Element N. `${a[-1]}` is the last one. |
| `"${a[@]}"` | Every element, each one as its own word. |
| `"${a[*]}"` | Every element as one piece of text, joined by a space. |
| `${#a[@]}` | How many elements there are. |
| `${#a[N]}` | The length of element N. |
| `${!a[@]}` | The indexes: `0 1 2`. |
| `${a[@]:START:COUNT}` | COUNT elements, starting at element START. |
| `$a` | The first element only, as in bash. |

An element that isn't there is empty text, not an error: `${hosts[9]}` gives nothing.

## Loop over an array

Use `"${hosts[@]}"` after `in`, with the quotes. Each element is then one turn of the loop, even
when it has spaces in it:

<!-- run -->
```
hosts=(web01 db01 "mail server")
for host in "${hosts[@]}"; do
  echo "checking $host"
done
```

<!-- output -->
```
checking web01
checking db01
checking mail server
```

This is what an array is better at than a plain variable with spaces in it (see
[Loop over a list](loops.md#loop-over-a-list)): `mail server` stays one item.

When you need the number of each element too, loop over the indexes:

<!-- run -->
```
hosts=(web01 db01 cache01)
for i in "${!hosts[@]}"; do
  echo "$i: ${hosts[$i]}"
done
```

<!-- output -->
```
0: web01
1: db01
2: cache01
```

To pass a whole array to a command or a function, write `"${hosts[@]}"` where the arguments go.
Each element arrives as one argument:

<!-- run -->
```
show() {
  echo "$# arguments, the last is $3"
}
hosts=(web01 db01 "mail server")
show "${hosts[@]}"
```

<!-- output -->
```
3 arguments, the last is mail server
```

## Add, change and remove

`+=( … )` adds at the end. `NAME[N]=VALUE` changes one element. `unset 'NAME[-1]'` drops the last
one:

<!-- run -->
```
todo=()
todo+=(backup)
todo+=(deploy notify)
echo "${todo[@]}"

todo[1]="deploy to web01"
echo "${todo[1]}"

unset 'todo[-1]'
echo "${#todo[@]} left: ${todo[*]}"
```

<!-- output -->
```
backup deploy notify
deploy to web01
2 left: backup deploy to web01
```

`todo=()` makes an empty array, which is a good way to start a list you fill in a loop. Put the
quotes around `'todo[-1]'` so the square brackets aren't taken for a file pattern.

A slice gives you a part of the array. `${todo[@]:1}` is everything from element 1 on, and
`${todo[@]:0:2}` is the first two:

<!-- run -->
```
letters=(a b c d e)
echo "${letters[@]:1}"
echo "${letters[@]:0:2}"
rest=("${letters[@]:2}")
echo "${#rest[@]} in rest"
```

<!-- output -->
```
b c d e
a b
3 in rest
```

To check whether an array is empty, look at its count:

<!-- run -->
```
found=()
if [ ${#found[@]} -eq 0 ]; then
  echo "nothing found"
fi
```

<!-- output -->
```
nothing found
```

## Fill an array from a file or a command

`mapfile -t NAME < FILE` reads every line of a file into an array, one element per line. The `-t`
drops the new line at the end of each one, and you nearly always want it:

<!-- run -->
```
printf 'web01\ndb01\ncache01\n' > hosts.txt

mapfile -t hosts < hosts.txt
echo "${#hosts[@]} hosts, the first is ${hosts[0]}"
```

<!-- output -->
```
3 hosts, the first is web01
```

`read -a NAME` splits one line into words, one element each:

<!-- run -->
```
read -a parts <<< "alice 1001 /home/alice"
echo "user ${parts[0]}, home ${parts[2]}"

IFS=: read -a fields <<< "deploy:x:1002:1002"
echo "id ${fields[2]}"
```

<!-- output -->
```
user alice, home /home/alice
id 1002
```

The words a command prints, or the files that match a pattern, go straight into the brackets:

<!-- run -->
```
touch app.log db.log notes.txt
logs=(*.log)
echo "${#logs[@]} log files: ${logs[*]}"

words=($(echo "one two three"))
echo "${words[1]}"
```

<!-- output -->
```
2 log files: app.log db.log
two
```

## Dicts: look a value up by name

A dict (bash calls it an associative array) uses a word where an array uses a number. Tell
ShellCraft it is a dict with `declare -A` before you put anything in it:

<!-- run -->
```
declare -A port
port[web]=80
port[db]=5432

echo "web is on ${port[web]}"
echo "${#port[@]} services: ${!port[@]}"

for name in "${!port[@]}"; do
  echo "$name -> ${port[$name]}"
done
```

<!-- output -->
```
web is on 80
2 services: web db
web -> 80
db -> 5432
```

`${!port[@]}` gives the keys and `"${port[@]}"` the values, both in the order you added them. A key
that isn't there gives empty text. You can set the whole dict in one line, and `unset` takes a key
away:

<!-- run -->
```
declare -A owner=([/srv/www]=alice [/srv/db]=deploy)
unset 'owner[/srv/db]'
echo "${!owner[@]}"
declare -p owner
```

<!-- output -->
```
/srv/www
declare -A owner=([/srv/www]="alice")
```

`declare -p NAME` prints an array or a dict in a form you can run again, which is handy when you
want to see what is in one.

Without the `declare -A` line you get `port: not a dict: declare -A port first`.

## The Python way

If you know Python, write a list with square brackets and commas, and use its methods. There are
spaces around the `=`, and text needs quotes:

<!-- run -->
```
hosts = ["web01", "db01"]
hosts.append("cache01")
hosts[0] = "web02"
print(hosts)
print(len(hosts), hosts[0], hosts[-1])

hosts.sort()
print(", ".join(hosts))

del hosts[0]
last = hosts.pop()
print(f"took {last}, left: {hosts}")
```

<!-- output -->
```
['web02', 'db01', 'cache01']
3 web02 cache01
cache01, db01, web02
took web02, left: ['db01']
```

A loop over a list is `for -py NAME in LIST`:

<!-- run -->
```
hosts = ["web01", "db01"]
for -py host in hosts:
    print(f"checking {host}")

for -py number, host in enumerate(hosts, 1):
    print(f"{number}. {host}")
```

<!-- output -->
```
checking web01
checking db01
1. web01
2. db01
```

A dict is written with curly brackets:

<!-- run -->
```
port = {"web": 80, "db": 5432}
port["cache"] = 6379
print(port["web"] + 1)
print("db" in port, port.get("mail"))

for -py name, number in port.items():
    print(f"{name} listens on {number}")

print(sorted(port))
```

<!-- output -->
```
81
True None
web listens on 80
db listens on 5432
cache listens on 6379
['cache', 'db', 'web']
```

Looping over a dict gives its keys. `.items()` gives each key with its value, as above.
`port["mail"]` on a key that isn't there stops with
`there is no key 'mail' (.get('mail') gives None for a missing key)`.

What you can use:

| | Reads | Changes the variable |
| --- | --- | --- |
| A list | `.index()` `.count()` `.copy()` | `.append()` `.extend()` `.insert()` `.pop()` `.remove()` `.clear()` `.sort()` `.reverse()` |
| A dict | `.keys()` `.values()` `.items()` `.get()` `.copy()` | `.pop()` `.update()` `.setdefault()` `.clear()` |

The functions `len`, `sorted`, `reversed`, `sum`, `min`, `max`, `any`, `all`, `enumerate`, `zip`,
`range`, `list` and `dict` take a list. So do slices (`hosts[1:]`), `"web01" in hosts`,
`", ".join(hosts)` and `"a b c".split()`, which gives you a list from text.

A method that changes the variable goes on a line of its own (`hosts.sort()`) or on the right of
an assignment (`last = hosts.pop()`). Inside a condition it is refused, so an `if` can never change
your list by accident.

A `while` loop that works through a list until it is empty:

<!-- run -->
```
todo = ["backup", "deploy", "notify"]
while -py (len(todo) > 0):
    task = todo.pop(0)
    print(f"doing {task}, {len(todo)} left")
```

<!-- output -->
```
doing backup, 2 left
doing deploy, 1 left
doing notify, 0 left
```

## One list, both styles

It doesn't matter which style made a list. Set it the bash way and read it the Python way, or the
other way round:

<!-- run -->
```
hosts=(web01 db01)
hosts.append("cache01")
print(hosts)

port = {"web": 80}
port[db]=5432
echo "${port[db]} ${!port[@]}"

if -py ("db01" in hosts) { echo "db01 is in the list" }
```

<!-- output -->
```
['web01', 'db01', 'cache01']
5432 web db
db01 is in the list
```

That last line is the easy way to ask "is this in my list?", which takes a loop in bash.

One thing to know: a value set the bash way is text, also when it looks like a number. Python then
sorts it as text and can't add it up. `map(int, LIST)` turns every element into a number:

<!-- run -->
```
sizes=(3 10 7)
print(sorted(sizes))
print(sorted(map(int, sizes)))
print(sum(map(int, sizes)))

counts = [3, 10, 7]
print(sum(counts))
```

<!-- output -->
```
['10', '3', '7']
[3, 7, 10]
20
20
```

Numbers you write in a Python list (`[3, 10, 7]`) stay numbers. In bash arithmetic an element is
always read as a number: `$(( sizes[0] + sizes[1] ))` is 13.

## Lists in functions

A function gets a **copy** of a list you pass it. Changing the copy doesn't change yours, so return
the list and store it:

<!-- run -->
```
def with_backup(items):
    items.append("backup01")
    return items

hosts = ["web01", "db01"]
longer = with_backup(hosts)
print(hosts)
print(longer)
```

<!-- output -->
```
['web01', 'db01']
['web01', 'db01', 'backup01']
```

The same goes for `b = a`: `b` is a copy, not a second name for the same list. This is different
from Python, and it means a function can never change your list behind your back.

A bash function takes the elements as arguments and can keep them in an array of its own with
`local`:

<!-- run -->
```
longest() {
  local words=("$@")
  local best=""
  for word in "${words[@]}"; do
    if [ ${#word} -gt ${#best} ]; then best=$word; fi
  done
  echo "$best"
}
names=(web01 cache01 db01)
longest "${names[@]}"
```

<!-- output -->
```
cache01
```

## What is different

From bash:

- An array has no gaps. `a[7]=x` on an array of three stops with
  `a[7]: arrays have no gaps (next index is 3)`, and `unset 'a[0]'` in the middle stops with
  `would leave a gap; use del a[0]`. `del a[0]` removes the element and moves the rest up.
- An array is not passed on to programs you start (bash doesn't pass it either), and `export hosts`
  says so instead of exporting only the first element. Export its text:
  `export HOSTS="${hosts[*]}"`.
- Operators on a whole array, like `${a[@]/x/y}` and `${a[@]^^}`, are not supported. Loop over it.
- `(( a[i]++ ))` is not supported. Write `a[i]=$(( a[i] + 1 ))`.
- `declare -ai`, `${!name}` and `mapfile` options other than `-t`, `-n`, `-s` and `-d` are not
  supported.

From Python:

- A list holds text and numbers, not another list or a dict.
- The keys of a dict are text: `{"1": "one"}`, not `{1: "one"}`.
- No list comprehensions, no `lambda` and no `key=`. Write a loop. `sorted(hosts, reverse=True)`
  and `hosts.sort(reverse=True)` do work.
- `b = a` and passing a list to a function make a copy.
- At the top of a file, a block that ends in `:` needs `-py` after its first word
  (`for -py host in hosts:`). Inside a `def` or another colon block you can leave it out. See
  [Bash style and Python style](python-style.md).

And one that catches everybody: `a[0]=x` without spaces is the bash form, and `a[0] = "x"` with
spaces is the Python one. In the Python form the text needs its quotes.

## If it doesn't work

| You see | What it means |
| --- | --- |
| Only the first element is printed | You wrote `$hosts`. Write `"${hosts[@]}"` for all of them. |
| The loop runs once with everything on one line | You wrote `"${hosts[*]}"` after `in`. Use `"${hosts[@]}"`. |
| `port: not a dict: declare -A port first` | A word as a key needs `declare -A port` before it. |
| `a[7]: arrays have no gaps (next index is 3)` | You set an element past the end. Add with `a+=(x)`. |
| `hosts: not set, give it a value first (hosts = [])` | You called `.append()` on a name that has no list yet. |
| `IndexError: list index out of range` | The Python form `hosts[9]` on a shorter list. `${hosts[9]}` gives empty text instead. |
| `a list can hold only text and numbers` | You put a list inside a list. Keep two lists, or a dict. |
| `'ListComp' is not allowed` | A list comprehension. Write a `for` loop with `.append()`. |
| `command not found: hosts.push(x)` | That method doesn't exist. The table above has the ones that do. |
| `missing ')'` | The closing bracket of an array is missing. `source -n FILE` finds it before you run the file. |
