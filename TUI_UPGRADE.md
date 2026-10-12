# TUI Architecture: Split Screens, Mouse Resizing & Multi-Pane Agents/Shells

This document outlines the architecture, terminal protocols, layout math, and implementation strategies for building a multi-pane Terminal User Interface (TUI) featuring mouse-draggable splits, interactive sub-shells, and streaming AI agents (similar to Claude Code, Zellij, and modern multiplexers).

---

## Table of Contents
1. [The Big Picture: Two Core Models](#1-the-big-picture-two-core-models)
2. [Terminal Wire Protocol: How Mouse Input Works](#2-terminal-wire-protocol-how-mouse-input-works)
3. [Draggable Split Screen Mechanics & Layout Math](#3-draggable-split-screen-mechanics--layout-math)
4. [Hosting Real Shells vs. AI Agent Streams](#4-hosting-real-shells-vs-ai-agent-streams)
5. [Implementation Architectures](#5-implementation-architectures)
   - [Option A: `prompt_toolkit` (ShellCraft's Native Stack)](#option-a-prompt_toolkit-shellcrafts-native-stack)
   - [Option B: `Textual` (Modern Python Component Model)](#option-b-textual-modern-python-component-model)
   - [Option C: React + Ink (Claude Code's Stack)](#option-c-react--ink-claude-codes-stack)
6. [Architectural Checklist & Best Practices](#6-architectural-checklist--best-practices)

---

## 1. The Big Picture: Two Core Models

When designing a multi-pane TUI for agents and shells, apps generally fall into one of two paradigms:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Terminal Multiplexer Model                      │
│                  (e.g., tmux, zellij, pymux)                           │
│                                                                        │
│  [ PTY Master ] ──> [ VT100 Emulator ] ──> [ Pane Matrix Bounding Box ]│
│  - Raw process isolation                                               │
│  - Keystrokes forwarded directly to child shells                       │
│  - Requires terminal emulation inside the TUI                          │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                     Declarative Component Tree Model                   │
│               (e.g., Claude Code via Ink/Yoga, Textual)                │
│                                                                        │
│  [ LLM Stream / Agent Loop ] ──> [ State ] ──> [ Flexbox/CSS Layout ]  │
│  - Virtual tree diffing flushed to alternate screen buffer             │
│  - Rich widgets: collapsible tool logs, diff views, markdown, spinners │
│  - Shell tasks executed as background jobs or embedded PTY panes       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Terminal Wire Protocol: How Mouse Input Works

Terminals communicate exclusively through byte streams. By default, mouse clicks are consumed locally by the terminal emulator for text selection. To receive mouse coordinates and drags in the application, the TUI must place the terminal in **raw mode** and activate mouse tracking escape sequences.

### Essential Escape Sequences

| Sequence | Purpose | Description |
| :--- | :--- | :--- |
| `\x1b[?1049h` | **Alternate Screen Buffer** | Takes over the full window (like `vim` or `htop`), preserving shell history on exit. |
| `\x1b[?1000h` | **Normal Mouse Tracking** | Reports button clicks (press and release). |
| `\x1b[?1002h` | **Button-Event Tracking** | Reports mouse **motion while a button is held down** (essential for dragging). |
| `\x1b[?1006h` | **SGR Extended Mode** | Eliminates the 223-column limitation and formats coordinates cleanly with standard ASCII integers. |

### The SGR Mouse Wire Protocol in Action

When SGR mode (`?1006h`) is active, terminal stdin receives structured messages:

```
Mouse Down (Left Button) at col 45, row 10:
  \x1b[< 0 ; 45 ; 10 M

Mouse Drag (Left Button held, moving) to col 46, row 10:
  \x1b[< 32 ; 46 ; 10 M      <-- Code 32 specifies button-1 drag motion

Mouse Up (Release) at col 52, row 10:
  \x1b[< 0 ; 52 ; 10 m       <-- Lowercase 'm' indicates button release
```

The TUI event loop translates these raw bytes into high-level event objects:
- `MouseDown(button=1, x=45, y=10)`
- `MouseMove(button=1, dragging=True, x=46, y=10)`
- `MouseUp(button=1, x=52, y=10)`

---

## 3. Draggable Split Screen Mechanics & Layout Math

Resizing a split layout using a mouse relies on three primary phases:

```
[ User clicks divider ] ──> [ Capture Mouse ] ──> [ Drag & Clamp X ] ──> [ Redraw & Signal Resize ]
```

### 1. Hit-Testing & Capture
- When `MouseDown` occurs, the engine checks whether `(x, y)` intersects the 1-character-wide divider boundary (`│` or `|`).
- Once clicked, the UI calls `capture_mouse(True)` (or flags `dragging = True` on the divider component). This ensures that even if the cursor moves faster than the rendering loop, subsequent drag and release events are guaranteed to route to the divider.

### 2. Coordinate Clamping
During mouse motion, the new width of the left container is computed relative to the container's screen origin:
$$\text{new\_width} = \text{clamp}(x_{\text{mouse}} - x_{\text{container\_start}}, \text{min\_width}, \text{max\_width})$$

### 3. Redraw & Subprocess Notification
- The layout engine recalculates pane dimensions (e.g., column allocations).
- An invalidation signal flags the screen buffer as dirty.
- The TUI paints into an in-memory grid, diffs against the previous frame, and flushes minimal ANSI cursor updates to the screen.
- If a child shell is embedded, a window resize signal (`TIOCSWINSZ`) is sent to its PTY.

---

## 4. Hosting Real Shells vs. AI Agent Streams

A common architectural challenge is supporting both raw interactive terminal shells and rich AI agent interaction.

| Dimension | Interactive Sub-Shell (`bash`, `zsh`, `htop`) | AI Agent (Claude Code / ShellCraft) |
| :--- | :--- | :--- |
| **Input Model** | Raw keystroke passthrough to stdin | Prompt editor, suggestion toolbar, button approvals |
| **Output Model** | Arbitrary ANSI cursor jumps, line clears, colors | Structured streaming markdown, collapsibles, progress spinners |
| **Backend Engine** | **Pseudo-Terminal (PTY)** + VT100 Emulator | Async event loop / Worker thread + Component Tree |
| **Resize Action** | Send `TIOCSWINSZ` (`SIGWINCH`) to child PTY | Re-wrap text containers in flexbox/grid layout |

### Embedding an Interactive Shell (PTY + VT100 Emulator)
To host a real shell inside a sub-pane:
1. **PTY Creation**: Open a pseudoterminal master/slave pair (`pty.openpty()` on Unix, `CreatePseudoConsole` / ConPTY on Windows).
2. **Subprocess Spawn**: Launch the shell connected to the slave PTY.
3. **In-Memory VT100 Emulation**: Because tools like `htop`, `vim`, or `bash` emit raw cursor positioning codes (`\x1b[2J`, `\x1b[H`), you cannot just stream stdout to a screen window. The master PTY output must be fed into an in-memory terminal emulator library (such as [`pyte`](https://github.com/selectel/pyte) in Python, [`xterm-headless`](https://www.npmjs.com/package/xterm-headless) in Node.js, or [`alacritty_terminal`](https://crates.io/crates/alacritty_terminal) in Rust).
4. **Bounding Box Blitting**: The 2D character matrix from the emulator is copied into the bounding box assigned to that pane in the TUI screen buffer.
5. **Dynamic Resizing**: When the user drags the mouse divider, execute an `ioctl` on the master PTY:
   ```python
   import fcntl, termios, struct
   fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", new_rows, new_cols, 0, 0))
   ```

### Embedding an AI Agent Stream
AI agent panes do not require a full PTY. Instead, they use structured state:
1. An asynchronous agent task streams tokens or tool invocation payloads across an `asyncio.Queue`.
2. The UI reconciles changes in user message history, active reasoning logs, tool call cards, and diff views.
3. Markdown formatting is rendered dynamically to fit the current pane width.

---

## 5. Implementation Architectures

### Option A: `prompt_toolkit` (ShellCraft's Native Stack)

ShellCraft already depends on `prompt_toolkit`. You can implement draggable split screens directly using `VSplit`, `Window`, `FormattedTextControl`, and custom mouse handlers:

```python
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.layout import VSplit, Window, FormattedTextControl, Layout
from prompt_toolkit.mouse_events import MouseEventType

class DraggableSplit:
    def __init__(self):
        self.left_width = 45

        # Left pane: AI Agent (logs, prompt, transcript)
        self.left_window = Window(
            FormattedTextControl("🤖 Agent Pane\nStreaming plan and tool executions..."),
            width=lambda: self.left_width
        )

        # 1-character divider with drag-handling mouse callback
        self.divider = Window(
            FormattedTextControl("│", mouse_handler=self._on_divider_mouse),
            width=1
        )

        # Right pane: Shell or Tool Execution output
        self.right_window = Window(
            FormattedTextControl("💻 Shell / Commands Pane\nCommand output here...")
        )

        self.container = VSplit([
            self.left_window,
            self.divider,
            self.right_window
        ])

    def _on_divider_mouse(self, mouse_event):
        """Intercepts mouse clicks and drags on the separator."""
        if mouse_event.event_type in (MouseEventType.MOUSE_DOWN, MouseEventType.DRAG):
            new_x = mouse_event.position.x
            # Add safety bounds
            if new_x > 15:
                self.left_width = new_x
                get_app().invalidate()  # Trigger screen re-render
            return None
        return NotImplemented

# Boilerplate runner
if __name__ == "__main__":
    split = DraggableSplit()
    app = Application(
        layout=Layout(split.container),
        mouse_support=True,
        full_screen=True
    )
    app.run()
```

---

### Option B: `Textual` (Modern Python Component Model)

If building an advanced standalone application, Textual provides a full CSS layout engine and mouse capture:

```python
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Static
from textual.events import MouseDown, MouseMove, MouseUp

class Splitter(Static):
    DEFAULT_CSS = """
    Splitter {
        width: 1;
        height: 100%;
        background: $primary;
    }
    Splitter:hover {
        background: $accent;
    }
    """

class ResizableSplit(Container):
    DEFAULT_CSS = """
    ResizableSplit {
        layout: horizontal;
        height: 100%;
        width: 100%;
    }
    #agent-pane {
        width: 60%;
        height: 100%;
        background: $surface;
    }
    #shell-pane {
        width: 1fr;
        height: 100%;
        background: $panel;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("🤖 AI Agent Pane (Streaming Markdown, Logs)", id="agent-pane")
        yield Splitter("│", id="divider")
        yield Static("💻 Shell / Output Pane", id="shell-pane")

    def on_mouse_down(self, event: MouseDown) -> None:
        if event.widget.id == "divider":
            self.dragging = True
            self.capture_mouse(True)  # Global drag capture
            event.stop()

    def on_mouse_up(self, event: MouseUp) -> None:
        if getattr(self, "dragging", False):
            self.dragging = False
            self.capture_mouse(False)
            event.stop()

    def on_mouse_move(self, event: MouseMove) -> None:
        if getattr(self, "dragging", False):
            agent_pane = self.query_one("#agent-pane")
            new_width = event.screen_x - self.screen_region.x
            if 15 < new_width < self.size.width - 15:
                agent_pane.styles.width = new_width
            event.stop()

class AppDemo(App):
    def compose(self) -> ComposeResult:
        yield ResizableSplit()

if __name__ == "__main__":
    AppDemo().run()
```

---

### Option C: React + Ink (Claude Code's Stack)

Anthropic’s Claude Code CLI uses **TypeScript + React + Ink**:
- **Ink**: Custom React reconciler targeting ANSI terminals.
- **Yoga**: Layout calculations are solved using standard Flexbox (`flexDirection: "row"`, `flexGrow`).
- **Alternate Screen Buffer**: Uses `/tui fullscreen` mode via ANSI switch `\x1b[?1049h` to eliminate scroll flickering.
- **Mouse Event Subscriptions**: Listens to raw terminal stdin streams, parses SGR sequences, and adjusts component state:

```tsx
import React, { useState } from 'react';
import { Box, Text, useInput, useStdout } from 'ink';

export const SplitScreen = () => {
  const [leftWidth, setLeftWidth] = useState(50); // percentage or columns

  return (
    <Box flexDirection="row" width="100%" height="100%">
      <Box width={`${leftWidth}%`} flexDirection="column">
        <Text bold color="cyan">Agent Conversation & Tools</Text>
      </Box>

      {/* Divider */}
      <Box width={1}>
        <Text color="gray">│</Text>
      </Box>

      <Box flexGrow={1} flexDirection="column">
        <Text bold color="green">Shell Output & Diffs</Text>
      </Box>
    </Box>
  );
};
```

---

## 6. Architectural Checklist & Best Practices

1. **Terminal Mode Safety**:
   - Always install an `atexit` or `finally` handler to restore terminal settings (`\x1b[?1049l` and `\x1b[?1000l\x1b[?1002l\x1b[?1006l`) so crashes do not leave the user's terminal broken.
2. **Shift-Key Passthrough**:
   - Most modern terminal emulators (Kitty, Alacritty, iTerm2, WezTerm) bypass application mouse reporting when the user holds `Shift`. Document this for users so they can still copy text natively when needed.
3. **Thread Safety**:
   - The TUI rendering loop runs on a main thread or event loop. Background agent processing and PTY reads must communicate with the main loop using thread-safe channels (`asyncio.Queue` or `app.call_from_thread()`).
4. **PTY Window Resizing**:
   - Always notify child shell processes of size changes using `TIOCSWINSZ` after mouse drag operations complete to prevent line-wrapping corruption in interactive command-line programs.
