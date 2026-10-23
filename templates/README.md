# Module templates

`example.py`, `example.md` and `example.skill` are a complete, working ShellCraft module (`example`,
a word counter), with comments explaining every rule. Copy them as a starting point:

```bash
cp templates/example.py    ~/.shellcraft/modules/wordfreq.py
cp templates/example.md    ~/.shellcraft/modules/wordfreq.md
cp templates/example.skill ~/.shellcraft/modules/wordfreq.skill
# then rename "example" to "wordfreq" in all three files
```

The guides are in the manual:

- [Your first module](../docs/modules/first-module.md): from three lines to a web API client, step by step
- [Ingest a script](../docs/modules/ingest.md): convert a script you already have
- [Add modules you already have](../docs/modules/adding-modules.md): drop the files in
- [Module reference](../docs/modules/reference.md): every rule, file and option
