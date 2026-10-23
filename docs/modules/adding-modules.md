# Add modules you already have

← [Manual](../README.md)

If you already have a module, from a colleague, a repository or another machine, you don't need
the ingest wizard. Copy the files into your modules folder and they work.

## Copy them in

A module is up to three files with the same name:

| File | What it is | Needed? |
| --- | --- | --- |
| `NAME.py` | The code | Yes |
| `NAME.md` | Its manual page, for `man NAME` | Recommended |
| `NAME.skill` | Its description for AI clients | Only if AI clients should use it |

Copy them into `~/.shellcraft/modules/`:

```bash
cp ghrepo.py ghrepo.md ghrepo.skill ~/.shellcraft/modules/
```

That's it. A running ShellCraft notices new and changed files and loads them at the next prompt,
so there's nothing to restart. If you copied them while it wasn't running, they're loaded when
it starts.

Several at once works the same way:

```bash
cp ~/team-modules/*.{py,md,skill} ~/.shellcraft/modules/
```

## Helper files

A file whose name starts with an underscore, like `_common.py`, is a helper: it's never a command,
and modules can import it with `import _common`. Copy helpers along with the modules that use them.
`modules` lists them in their own table.

## Check what you added

```
ingest check --all            # every module ShellCraft loads, with what's wrong if anything
modules                       # what's loaded, and whether it's yours or bundled
which ghrepo                  # where a command comes from
```

If a module doesn't show up, [Troubleshooting](../troubleshooting.md#my-module-doesnt-show-up)
walks through the usual causes.

## Your modules and the bundled ones

ShellCraft loads modules from two places: the ones that come with it, and yours in
`~/.shellcraft/modules`. A module of yours with the same name as a bundled one wins, which is how
you replace a bundled module with your own version. `modules` shows `user` or `bundled` next to
each one.

Keep your modules in `~/.shellcraft/modules` rather than in ShellCraft's own folder. That way they
survive updates and reinstalls.

## Switch modules off and on

```
modules disable queryCensys     # no longer a command or an AI tool, and never loaded
modules enable queryCensys      # back, at once
modules -a                      # every module, with an on or off column
```

The choice is saved, and applies to the MCP server too.

## Keep a module away from AI clients

A module with a `.skill` file is offered to connected AI clients. To keep one for your own use
only, for example because it spends money or deletes things, put this at the top of its `.skill`
file:

```toml
expose = false
```

## Share your modules

Your modules are plain files, so share them any way you like: a git repository, a shared folder,
a zip. To use a whole folder of modules on every machine, keep them in a repository and link it:

```bash
git clone https://example.com/you/my-modules.git ~/my-modules
ln -s ~/my-modules/*.{py,md,skill} ~/.shellcraft/modules/
```
