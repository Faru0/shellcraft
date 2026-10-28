# Add modules you already have

← [Manual](../README.md)

This page is for you if someone handed you a ShellCraft module, or you want the modules from one
machine on another. After reading it you'll know where the files go, how to check they loaded,
how to switch a module off, and how to share your own.

If you already have a module, from a colleague, a repository or another machine, you don't need
the [ingest wizard](ingest.md): that's for plain Python scripts. A module is already in the right
shape. Copy the files into your modules folder and they work.

## Copy them in

A module is up to three files with the same name. Only the `.py` file is required, but copy all
the ones you were given:

| File | What it is | Needed? |
| --- | --- | --- |
| `NAME.py` | The code | Yes |
| `NAME.md` | Its manual page, for `man NAME` | Recommended |
| `NAME.skill` | Its description for AI clients | Only if AI clients should use it |

Copy them into `~/.shellcraft/modules/`:

```bash
cp ghrepo.py ghrepo.md ghrepo.skill ~/.shellcraft/modules/
```

A running ShellCraft notices new and changed files and loads them at the next prompt, so there's
nothing to restart. You'll see `↻ modules reloaded (ghrepo.md, ghrepo.py, ghrepo.skill)` above the
prompt. If you copied them while it wasn't running, they're loaded when it starts.

On Windows, the folder is `C:\Users\you\.shellcraft\modules\`. Copy the files there with
Explorer or `copy`.

Several at once works the same way. The `{py,md,skill}` part is bash shorthand for "each of these
three endings", so this copies every module file in the folder:

```bash
cp ~/team-modules/*.{py,md,skill} ~/.shellcraft/modules/
```

## Helper files

Some modules share code. A file whose name starts with an underscore, like `_common.py`, is a
helper: it's never a command, and modules can import it with `import _common`. Copy helpers along
with the modules that use them, or those modules fail to load with a `ModuleNotFoundError`.
`modules` lists the helpers in their own table, with the modules that import them.

## Check what you added

A module that fails to load doesn't stop the shell, it's skipped with a warning. So after copying
modules in, it's worth one quick check. These three commands answer "did it load?", "is anything
wrong with it?" and "which `ghrepo` am I running?":

```
ingest check --all            # every module ShellCraft loads, with what's wrong if anything
modules                       # what's loaded, and whether it's yours or bundled
which ghrepo                  # where a command comes from
```

If a module doesn't show up, [Troubleshooting](../troubleshooting.md#my-module-doesnt-show-up)
walks through the usual causes.

## Your modules and the bundled ones

ShellCraft loads modules from two places: the ones that come with it, and yours in
`~/.shellcraft/modules`. A module of yours with the same name as a bundled one wins. That's how you
replace a bundled module with your own version: copy it into your folder, edit the copy, and yours
is the one that runs. `modules` shows `user` or `bundled` next to each one.

Keep your modules in `~/.shellcraft/modules` rather than in ShellCraft's own folder. That way they
survive updates and reinstalls.

## Switch modules off and on

Sometimes you want a module gone without deleting it: it needs an API key you don't have, it clashes
with something, or you only use it now and then. Switch it off instead:

```
modules disable queryCensys     # no longer a command or an AI tool, and never loaded
modules enable queryCensys      # back, at once
modules -a                      # every module, with an on or off column
```

The choice is saved, and applies to the MCP server too, so an AI client stops seeing it as well.

## Keep a module away from AI clients

A module with a `.skill` file is offered to connected AI clients (see
[MCP and the AI co-pilot](../mcp-and-ai.md)). To keep one for your own use only, for example because
it spends money or deletes things, put this at the top of its `.skill` file:

```toml
expose = false
```

It has to go before the first `[[params]]` line. In TOML, a line after a `[[params]]` header
belongs to that table, so `expose = false` there would be ignored without a word.

## Share your modules

Your modules are plain files, so share them any way you like: a git repository, a shared folder,
a zip. To use a whole folder of modules on every machine, keep them in a repository and link it.
A link means the files stay in the repository, so a `git pull` updates your commands too:

```bash
git clone https://example.com/you/my-modules.git ~/my-modules
ln -s ~/my-modules/*.{py,md,skill} ~/.shellcraft/modules/
```

Before you share a module, run `ingest check NAME.py --strict` on it, and check it reads its API key from
the environment rather than having it written in the code.
