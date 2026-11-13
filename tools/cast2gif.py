"""Turn an asciinema v2 `.cast` into a GIF, with nothing but pyte and Pillow.

    python tools/cast2gif.py docs/demo3.cast                 writes docs/demo3.gif
    python tools/cast2gif.py docs/demo3.cast -o /tmp/x.gif   somewhere else
    python tools/cast2gif.py docs/demo3.cast --frame 41.5 -o /tmp/x.png    one still, at that second

It is what made docs/demo3.gif. `agg` (https://github.com/asciinema/agg) does the same job and made
docs/demo.gif; this one is for a machine without it. It replays the recording through a terminal
emulator (pyte), draws every screen with a monospace font (Pillow), and writes the frames with one
shared palette. The colours are agg's default theme, so both demos look alike.

It needs `pyte` (in the project's dev install) and `Pillow` (not a dependency of ShellCraft:
`pip install pillow`, or run it with a Python that has it).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyte
from PIL import Image, ImageDraw, ImageFont

CELL_W, CELL_H = 10, 22  # one character, in pixels
FONT_SIZE = CELL_W / 0.6  # the fonts below are 0.6 em wide
PAD = 10
SMOOTH = 4  # box-drawing characters are drawn this many times larger, then scaled down
GAP = 0.03  # seconds: output closer together than this is one frame
IDLE = 3.0  # no frame stays longer than this
LAST = 3.0  # how long the last frame stays

# Regular, bold, italic, bold italic. The first family that is installed is used.
FAMILIES = [
    ("/usr/share/fonts/adwaita-mono-fonts/AdwaitaMono-{}.ttf", ("Regular", "Bold", "Italic", "BoldItalic")),
    ("/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono{}.ttf", ("", "-Bold", "-Oblique", "-BoldOblique")),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono{}.ttf", ("", "-Bold", "-Oblique", "-BoldOblique")),
    ("/usr/share/fonts/liberation-mono-fonts/LiberationMono-{}.ttf", ("Regular", "Bold", "Italic", "BoldItalic")),
]

BACKGROUND, FOREGROUND = "282a36", "f8f8f2"  # agg's default theme (Dracula)
NAMED = dict(zip(
    ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white"),
    ("21222c", "ff5555", "50fa7b", "f1fa8c", "bd93f9", "ff79c6", "8be9fd", "f8f8f2"), strict=True))
NAMED.update(zip(
    ("brightblack", "brightred", "brightgreen", "brightbrown", "brightblue", "brightmagenta", "brightcyan",
     "brightwhite"),
    ("6272a4", "ff6e6e", "69ff94", "ffffa5", "d6acff", "ff92df", "a4ffff", "ffffff"), strict=True))

# Lines the font would leave gaps in: the arms of each character as (left, right, up, down),
# 0 none, 1 light, 2 heavy, 3 double.
ARMS = {
    "─": (1, 1, 0, 0), "━": (2, 2, 0, 0), "│": (0, 0, 1, 1), "┃": (0, 0, 2, 2), "═": (3, 3, 0, 0), "║": (0, 0, 3, 3),
    "┌": (0, 1, 0, 1), "┐": (1, 0, 0, 1), "└": (0, 1, 1, 0), "┘": (1, 0, 1, 0),
    "├": (0, 1, 1, 1), "┤": (1, 0, 1, 1), "┬": (1, 1, 0, 1), "┴": (1, 1, 1, 0), "┼": (1, 1, 1, 1),
    "┏": (0, 2, 0, 2), "┓": (2, 0, 0, 2), "┗": (0, 2, 2, 0), "┛": (2, 0, 2, 0),
    "┣": (0, 2, 2, 2), "┫": (2, 0, 2, 2), "┳": (2, 2, 0, 2), "┻": (2, 2, 2, 0), "╋": (2, 2, 2, 2),
    "┡": (0, 2, 2, 1), "┩": (2, 0, 2, 1), "╇": (2, 2, 2, 1), "┢": (0, 2, 1, 2), "┪": (2, 0, 1, 2), "╈": (2, 2, 1, 2),
    "╔": (0, 3, 0, 3), "╗": (3, 0, 0, 3), "╚": (0, 3, 3, 0), "╝": (3, 0, 3, 0),
}
ROUND = {"╭": (1, 1), "╮": (-1, 1), "╯": (-1, -1), "╰": (1, -1)}  # which way the arms go: (x, y)

Colour = tuple[int, int, int]


def rgb(name: str, default: str) -> Colour:
    text = NAMED.get(name, name if len(name) == 6 else default)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return rgb(default, default)


def fonts() -> list[ImageFont.FreeTypeFont]:
    for pattern, styles in FAMILIES:
        paths = [Path(pattern.format(style)) for style in styles]
        if all(path.is_file() for path in paths):
            return [ImageFont.truetype(str(path), FONT_SIZE) for path in paths]
    raise SystemExit("tools/cast2gif.py: no monospace font found; add yours to FAMILIES")


class Painter:
    """Draws rows of a pyte screen; a row drawn once is kept."""

    def __init__(self, columns: int) -> None:
        self.columns = columns
        self.fonts = fonts()
        ascent, descent = self.fonts[0].getmetrics()
        self.baseline = (CELL_H - ascent - descent) // 2 + ascent
        self.rows: dict[tuple[Any, ...], Image.Image] = {}
        self.tiles: dict[tuple[str, Colour, Colour], Image.Image] = {}

    def key(self, line: Any, cursor: int | None) -> tuple[Any, ...]:
        """What decides how a row looks: its cells, and the cursor when it is on it."""
        cells = []
        for column in range(self.columns):
            cell = line[column]
            fg, bg = rgb(cell.fg, FOREGROUND), rgb(cell.bg, BACKGROUND)
            if cell.reverse != (column == cursor):
                fg, bg = bg, fg
            cells.append((cell.data, fg, bg, cell.bold, cell.italics, cell.underscore))
        return tuple(cells)

    def row(self, key: tuple[Any, ...]) -> Image.Image:
        if key not in self.rows:
            image = Image.new("RGB", (self.columns * CELL_W, CELL_H), rgb(BACKGROUND, BACKGROUND))
            draw = ImageDraw.Draw(image)
            for column, (_, _, bg, *_) in enumerate(key):  # every background first: a glyph may lean over
                if bg != rgb(BACKGROUND, BACKGROUND):
                    draw.rectangle((column * CELL_W, 0, (column + 1) * CELL_W - 1, CELL_H - 1), fill=bg)
            for column, (text, fg, bg, bold, italics, underscore) in enumerate(key):
                x = column * CELL_W
                if text in ARMS or text in ROUND or text == "█":
                    image.paste(self.tile(text, fg, bg), (x, 0))
                elif text.strip():
                    draw.text((x, self.baseline), text, font=self.fonts[bold + 2 * italics], fill=fg, anchor="ls")
                if underscore:
                    draw.line((x, self.baseline + 2, x + CELL_W - 1, self.baseline + 2), fill=fg)
            self.rows[key] = image
        return self.rows[key]

    def tile(self, text: str, fg: Colour, bg: Colour) -> Image.Image:
        """A line-drawing character, drawn edge to edge so that neighbours join."""
        if (text, fg, bg) not in self.tiles:
            width, height = CELL_W * SMOOTH, CELL_H * SMOOTH
            image = Image.new("RGB", (width, height), fg if text == "█" else bg)
            draw = ImageDraw.Draw(image)
            cx, cy = width // 2, height // 2
            light, heavy = round(1.5 * SMOOTH), 3 * SMOOTH

            def bar(x0: float, y0: float, x1: float, y1: float) -> None:
                draw.rectangle((min(x0, x1), min(y0, y1), max(x0, x1) - 1, max(y0, y1) - 1), fill=fg)

            if text in ROUND:
                sx, sy = ROUND[text]
                radius, half = width // 2, light / 2
                ox, oy = cx + sx * radius, cy + sy * radius  # the centre of the quarter circle
                start = {(1, 1): 180, (-1, 1): 270, (-1, -1): 0, (1, -1): 90}[(sx, sy)]
                draw.arc((ox - radius - half, oy - radius - half, ox + radius + half, oy + radius + half),
                         start, start + 90, fill=fg, width=light)
                bar(cx - half, oy, cx + half, height if sy > 0 else 0)
            elif text in ARMS:
                left, right, up, down = ARMS[text]
                if 3 in (left, right, up, down):
                    self.double(bar, (left, right, up, down), (cx, cy, width, height), light)
                else:
                    thick = {0: 0, 1: light, 2: heavy}
                    across, along = max(thick[left], thick[right]) / 2, max(thick[up], thick[down]) / 2
                    if left:
                        bar(0, cy - thick[left] / 2, cx + along, cy + thick[left] / 2)
                    if right:
                        bar(cx - along, cy - thick[right] / 2, width, cy + thick[right] / 2)
                    if up:
                        bar(cx - thick[up] / 2, 0, cx + thick[up] / 2, cy + across)
                    if down:
                        bar(cx - thick[down] / 2, cy - across, cx + thick[down] / 2, height)
            self.tiles[(text, fg, bg)] = image.resize((CELL_W, CELL_H), Image.Resampling.LANCZOS)
        return self.tiles[(text, fg, bg)]

    @staticmethod
    def double(bar: Any, arms: tuple[int, int, int, int], box: tuple[int, int, int, int], thick: int) -> None:
        left, right, up, down = arms
        cx, cy, width, height = box
        half = thick / 2
        if left and right:
            for y in (cy - thick, cy + thick):
                bar(0, y - half, width, y + half)
        elif up and down:
            for x in (cx - thick, cx + thick):
                bar(x - half, 0, x + half, height)
        else:  # a corner: an outer and an inner line
            sx, sy = (1 if right else -1), (1 if down else -1)
            for side in (-1, 1):
                x, y = cx + side * sx * thick, cy + side * sy * thick
                bar(x - sx * half, y - half, width if sx > 0 else 0, y + half)
                bar(x - half, y - sy * half, x + half, height if sy > 0 else 0)


def shots(cast: Path) -> tuple[int, int, list[tuple[float, tuple[Any, ...]]], Painter]:
    """The screens of the recording as (second it appears, the key of every row)."""
    lines = cast.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    columns, rows = head["width"], head["height"]
    screen = pyte.Screen(columns, rows)
    stream = pyte.Stream(screen)
    painter = Painter(columns)
    found: list[tuple[float, tuple[Any, ...]]] = []

    def shot(when: float) -> None:
        cursor = None if screen.cursor.hidden else (screen.cursor.x, screen.cursor.y)
        keys = tuple(painter.key(screen.buffer[y], cursor[0] if cursor and cursor[1] == y else None)
                     for y in range(rows))
        if not found or found[-1][1] != keys:
            found.append((when, keys))

    pending: float | None = None
    for line in lines[1:]:
        when, kind, text = json.loads(line)
        if kind != "o":
            continue
        if pending is not None and when - pending >= GAP:
            shot(pending)
            pending = None
        stream.feed(text)
        if pending is None:
            pending = when
    if pending is not None:
        shot(pending)
    return columns, rows, found, painter


def palette(images: list[Image.Image]) -> Image.Image:
    """One palette for every frame: the most used colours as they are, the rest by median cut."""
    counts: Counter[Colour] = Counter()
    for image in images:
        for count, colour in image.getcolors(maxcolors=image.width * image.height) or []:
            counts[colour] += count
    kept = [colour for colour, _ in counts.most_common(256)]
    if len(counts) > 256:
        kept = kept[:32]
        rest = [colour for colour in counts if colour not in set(kept)]
        strip = Image.new("RGB", (len(rest), 1))
        strip.putdata(rest)
        cut = strip.quantize(256 - len(kept), method=Image.Quantize.MEDIANCUT).getpalette() or []
        kept += [(cut[i], cut[i + 1], cut[i + 2]) for i in range(0, 3 * (256 - len(kept)), 3)]
    holder = Image.new("P", (1, 1))
    holder.putpalette([part for colour in kept for part in colour] + [0] * (768 - 3 * len(kept)))
    return holder


def frames(columns: int, rows: int, found: list[tuple[float, tuple[Any, ...]]],
           painter: Painter, holder: Image.Image) -> Iterator[Image.Image]:
    size = (columns * CELL_W + 2 * PAD, rows * CELL_H + 2 * PAD)
    ground = Image.new("RGB", size, rgb(BACKGROUND, BACKGROUND)).quantize(palette=holder, dither=Image.Dither.NONE)
    strips: dict[tuple[Any, ...], Image.Image] = {}
    for _, keys in found:
        frame = ground.copy()
        for y, key in enumerate(keys):
            if key not in strips:
                strips[key] = painter.row(key).quantize(palette=holder, dither=Image.Dither.NONE)
            frame.paste(strips[key], (PAD, PAD + y * CELL_H))
        yield frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("cast", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="default: the cast's name with .gif")
    parser.add_argument("--frame", type=float, metavar="SECOND", help="write one still (PNG) instead of the GIF")
    args = parser.parse_args(argv)
    columns, rows, found, painter = shots(args.cast)
    if not found:
        print(f"tools/cast2gif.py: {args.cast} has no output in it", file=sys.stderr)
        return 1
    if args.frame is not None:
        found = [[shot for shot in found if shot[0] <= args.frame][-1:] or found[:1]][0]
    for _, keys in found:
        for key in keys:
            painter.row(key)
    holder = palette(list(painter.rows.values()))
    output = args.output or args.cast.with_suffix(".png" if args.frame is not None else ".gif")
    drawn = frames(columns, rows, found, painter, holder)
    first = next(drawn)
    if args.frame is not None:
        first.save(output)
        print(f"wrote {output}")
        return 0
    # GIF counts in hundredths of a second: round each frame, and carry what the rounding left over.
    times = [when for when, _ in found]
    lengths, owed = [], 0.0
    for when, after in zip(times, [*times[1:], times[-1] + LAST], strict=True):
        owed += min(after - when, IDLE)
        length = max(2, round(owed * 100))
        owed -= length / 100
        lengths.append(length * 10)
    first.save(output, save_all=True, append_images=drawn, duration=lengths, loop=0, disposal=1, optimize=False)
    print(f"wrote {output}: {len(found)} frames, {sum(lengths) / 1000:.0f} s, {output.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
