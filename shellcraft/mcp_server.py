"""Expose loaded modules as MCP tools, over stdio or streamable HTTP.

Each module becomes one tool whose description is its parsed .skill file; a module that declares
.skill [[params]] gets a typed input schema, otherwise a raw CLI `args` array. A
`shellcraft_pipeline` tool additionally runs full `a | b | c` command lines. Every man page is
also a resource (`shellcraft://man/<name>`). When the modules directory changes, the registry
reloads and connected clients get tools/list_changed and resources/list_changed.
Over stdio, nothing may be written to stdout except MCP protocol traffic.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import sys
import weakref
from typing import Any, Callable, Iterable

import anyio
import mcp_types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import (
    InMemorySubscriptionBus,
    ListenHandler,
    ResourcesListChanged,
    ResourceUpdated,
    ToolsListChanged,
)
from mcp.shared.exceptions import MCPError

from shellcraft import __version__
from shellcraft.builtins import BUILTINS, manual_text
from shellcraft.context import CommandError, ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.modkit import ModuleError
from shellcraft.params import ParamError
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.watch import ModuleWatcher

log = logging.getLogger("shellcraft.mcp")

FRESH_PROPERTY: dict[str, Any] = {
    "type": "boolean", "default": False,
    "description": "Ask the service again instead of reusing an answer from the last few minutes."}
AGENT_PROPERTY: dict[str, Any] = {
    "type": "string", "maxLength": 40,
    "description": "Optional: your name or role in this session (\"worker-2\"). It shows in the user's footer, "
                   "on the task board and in the audit log, so the user can tell agents apart."}
CREATE_TASK_TOOL = "create_task"
LIST_TASKS_TOOL = "list_tasks"
CLAIM_TASK_TOOL = "claim_task"
UPDATE_TASK_TOOL = "update_task"
RELEASE_TASK_TOOL = "release_task"
TASK_TOOLS = (CREATE_TASK_TOOL, LIST_TASKS_TOOL, CLAIM_TASK_TOOL, UPDATE_TASK_TOOL, RELEASE_TASK_TOOL)
TASKS_URI = "shellcraft://tasks"
TASK_SCHEMAS: dict[str, dict[str, Any]] = {
    CREATE_TASK_TOOL: {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 200, "description": "What is to be done, in one line."},
            "description": {"type": "string", "maxLength": 20000,
                            "description": "Everything a worker needs: where to look, what done means."},
            "parent": {"type": "string", "description": "The task this is a part of."},
            "depends_on": {"type": "array", "items": {"type": "string"}, "maxItems": 20,
                           "description": "Task ids that must be done before this one can be claimed."},
            "source": {"type": "string", "description": "The review item (a flag or a message) this comes from."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
    LIST_TASKS_TOOL: {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "One task in full (with its notes and result)."},
            "status": {"type": "string", "default": "active",
                       "enum": ["active", "all", "open", "claimed", "working", "blocked", "done", "failed",
                                "cancelled"]},
            "owner": {"type": "string", "description": "Only this agent's tasks."},
            "parent": {"type": "string"},
            "claimable": {"type": "boolean", "default": False, "description": "Only tasks you could claim now."},
        },
        "additionalProperties": False,
    },
    CLAIM_TASK_TOOL: {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task to take; leave it out to take the most urgent "
                                                           "free one."},
            "lease_seconds": {"type": "integer", "minimum": 10, "maximum": 3600, "default": 300,
                              "description": "How long the claim lasts without an update. Every update renews it; "
                                             "a claim nobody renews can be taken by someone else."},
        },
        "additionalProperties": False,
    },
    UPDATE_TASK_TOOL: {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "status": {"type": "string", "enum": ["working", "blocked", "done", "failed"]},
            "note": {"type": "string", "maxLength": 20000, "description": "Progress, or why it is blocked."},
            "result": {"type": "string", "maxLength": 20000, "description": "What you found or made."},
            "lease_seconds": {"type": "integer", "minimum": 10, "maximum": 3600, "default": 300},
        },
        "required": ["task_id"],
        "additionalProperties": False,
    },
    RELEASE_TASK_TOOL: {
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "note": {"type": "string", "maxLength": 20000}},
        "required": ["task_id"],
        "additionalProperties": False,
    },
}
TASK_DESCRIPTIONS = {
    CREATE_TASK_TOOL: "Put a task on the shared board for a worker to claim (an orchestrator splits work this way). "
                      "It shows in the user's prompt as `✦ N tasks`. Say what done means in the description.",
    LIST_TASKS_TOOL: "The tasks on the board with their status, owner, notes and what each waits for. Use "
                     "claimable=true to see what you could take now.",
    CLAIM_TASK_TOOL: "Take a task so no other agent does: exclusive, for a lease that every update renews. "
                     "Without task_id you get the most urgent free one. Pass your `agent` name.",
    UPDATE_TASK_TOOL: "Report on a task you hold: a note, status working, blocked (the user is told), done or "
                      "failed, and a result. Each call renews your lease.",
    RELEASE_TASK_TOOL: "Give a task back so another agent can take it.",
}
ADD_FINDING_TOOL = "add_finding"
LIST_FINDINGS_TOOL = "list_findings"
GET_FINDING_TOOL = "get_finding"
UPDATE_FINDING_TOOL = "update_finding"
FINDING_TOOLS = (ADD_FINDING_TOOL, LIST_FINDINGS_TOOL, GET_FINDING_TOOL, UPDATE_FINDING_TOOL)
_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "array", "maxItems": 20,
    "description": "What shows it: each piece is a command and its output, or a note. Quote the exact lines.",
    "items": {"type": "object",
              "properties": {"command": {"type": "string"}, "output": {"type": "string"}, "note": {"type": "string"}},
              "additionalProperties": False}}
FINDING_SCHEMAS: dict[str, dict[str, Any]] = {
    ADD_FINDING_TOOL: {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["ip", "domain", "url", "hash", "user", "host", "file", "process",
                                                "other"], "description": "What sort of thing the finding is about."},
            "value": {"type": "string", "maxLength": 500,
                      "description": "The thing itself: the IP, the domain, the hash."},
            "title": {"type": "string", "maxLength": 200, "description": "What is wrong or notable, in a line."},
            "summary": {"type": "string", "maxLength": 20000},
            "evidence": _EVIDENCE_SCHEMA,
            "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"], "default": "info"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
            "task_id": {"type": "string", "description": "The board task this came from."},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "case": {"type": "string", "description": "The case to file it in (default: the current one)."},
        },
        "required": ["kind", "value"],
        "additionalProperties": False,
    },
    LIST_FINDINGS_TOOL: {
        "type": "object",
        "properties": {
            "case": {"type": "string"},
            "kind": {"type": "string"},
            "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"],
                         "description": "At least this severe."},
            "status": {"type": "string", "enum": ["open", "confirmed", "dismissed"]},
            "tag": {"type": "string"},
            "agent": {"type": "string", "description": "Only what this agent found."},
            "query": {"type": "string", "description": "Words in the value, title or summary."},
        },
        "additionalProperties": False,
    },
    GET_FINDING_TOOL: {
        "type": "object",
        "properties": {"finding_id": {"type": "string"}},
        "required": ["finding_id"],
        "additionalProperties": False,
    },
    UPDATE_FINDING_TOOL: {
        "type": "object",
        "properties": {
            "finding_id": {"type": "string"},
            "title": {"type": "string", "maxLength": 200},
            "summary": {"type": "string", "maxLength": 20000, "description": "Replaces the summary."},
            "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "status": {"type": "string", "enum": ["open", "confirmed", "dismissed"]},
            "add_tags": {"type": "array", "items": {"type": "string"}},
            "remove_tags": {"type": "array", "items": {"type": "string"}},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["finding_id"],
        "additionalProperties": False,
    },
}
FINDING_DESCRIPTIONS = {
    ADD_FINDING_TOOL: "Record a finding in the shared case file: something about an IP, domain, hash, user or "
                      "host, with the evidence. If another agent already recorded the same thing, yours is "
                      "merged into it (more evidence, the higher severity) and `created` is false, so check "
                      "list_findings first and add only what is new. The user reads the case with `findings`.",
    LIST_FINDINGS_TOOL: "The findings in the case, most severe first, without their evidence. Use it before "
                        "you investigate something, so you do not repeat another agent's work.",
    GET_FINDING_TOOL: "One finding in full, with all its evidence.",
    UPDATE_FINDING_TOOL: "Change a finding: its wording, severity, confidence, status (confirmed, dismissed) or "
                         "tags, or add evidence to it.",
}
PIPELINE_TOOL = "shellcraft_pipeline"
ALERT_TOOL = "send_tui_alert"
ALERT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string",
                    "description": "The alert text (one line; up to 300 characters)."},
        "severity": {"type": "string", "enum": ["info", "warn", "crit", "warning", "critical"], "default": "info",
                     "description": "info (green), warn (amber) or crit (red, stays until dismissed); the long "
                                    "names warning and critical work too."},
        "timeout_seconds": {"type": "integer", "minimum": 1,
                            "description": "Seconds to show it. Default: 15 for info, 60 for warn; crit stays."},
        "content": {"type": "string",
                    "description": "Optional payload the user can copy with a right-click on the alert and paste "
                                   "with Alt-v: a path, a command to run, a URL, JSON (up to 64 KB)."},
    },
    "required": ["message"],
}
ALERT_DESCRIPTION = ("Show a short alert in the user's ShellCraft terminal status panel (green info, amber warning, "
                     "red critical). Use it to flag something the user should see now: a finished task, a risk, "
                     "a question waiting. It is picked up by the interactive ShellCraft when one is running.")
MAN_URI = "shellcraft://man/"
WATCH_INTERVAL = 1.0  # seconds between checks of the modules directory
REVIEW_INTERVAL = 1.0  # seconds between checks of the review spool (shellcraft.review_spool)
REVIEW_QUEUE_URI = "shellcraft://review/queue"
GET_FLAGGED_TOOL = "get_flagged_items"
REQUEST_REVIEW_TOOL = "request_human_review"
REVIEW_STATUS_TOOL = "get_review_status"
RESOLVE_TOOL = "resolve_review_item"
ASK_USER_TOOL = "ask_user"
REVIEW_TOOLS = (GET_FLAGGED_TOOL, REQUEST_REVIEW_TOOL, REVIEW_STATUS_TOOL, RESOLVE_TOOL, ASK_USER_TOOL)
ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "maxLength": 500, "description": "The question, in a line."},
        "details": {"type": "string", "maxLength": 20000,
                    "description": "What the user needs to decide: what you found, the options, what each costs."},
        "choices": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 10,
                    "description": "Ready answers. The user can pick one by number or type their own words."},
        "priority": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
        "about": {"type": "string", "description": "The review item (a flag, a request) this is about; the "
                                                  "question is added to its thread."},
        "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 600, "default": 120,
                         "description": "Wait up to this long for the answer; 0 returns at once, then use "
                                        "get_review_status."},
    },
    "required": ["question"],
    "additionalProperties": False,
}
GET_FLAGGED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pending", "approved", "rejected", "answered", "resolved", "all"],
                   "default": "pending"},
        "kind": {"type": "string", "enum": ["flag", "review", "question", "all"], "default": "flag"},
        "include_artifacts": {"type": "boolean", "default": True},
    },
    "additionalProperties": False,
}
REQUEST_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 200, "description": "What you want to do, in one line."},
        "description": {"type": "string", "maxLength": 20000,
                        "description": "Why, what it changes, and what could go wrong."},
        "artifacts": {"type": "array", "maxItems": 20, "description": "The commands, diffs or files involved.",
                      "items": {"type": "object",
                                "properties": {"name": {"type": "string"},
                                               "kind": {"type": "string", "enum": ["text", "diff", "file"]},
                                               "content": {"type": "string"}},
                                "required": ["name", "content"], "additionalProperties": False}},
        "priority": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
        "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 600, "default": 0,
                         "description": "Wait up to this long for the answer (capped below the call timeout); "
                                        "0 returns at once, then use get_review_status."},
    },
    "required": ["title", "description"],
    "additionalProperties": False,
}
GET_REVIEW_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object", "properties": {"item_id": {"type": "string"}},
    "required": ["item_id"], "additionalProperties": False,
}
RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"item_id": {"type": "string"},
                   "resolution_notes": {"type": "string", "maxLength": 20000}},
    "required": ["item_id", "resolution_notes"], "additionalProperties": False,
}
REVIEW_DESCRIPTIONS = {
    GET_FLAGGED_TOOL: "Read the items in ShellCraft's review spool: what the user flagged for you with `mark` "
                      "(test failures, logs, diffs; kind \"flag\") and your own review requests (kind \"review\"). "
                      "Returns JSON, newest first. Call resolve_review_item when you have dealt with a flag.",
    REQUEST_REVIEW_TOOL: "Ask the user to approve an action before you take it: anything destructive or hard to "
                         "undo (deleting files, rewriting history, migrations, force pushes). The request shows "
                         "as an alert in the user's ShellCraft; they answer with `review approve` or `review "
                         "reject` and notes. Go ahead only when the status is \"approved\". With wait_seconds the "
                         "call returns when they answer; otherwise poll get_review_status.",
    REVIEW_STATUS_TOOL: "The status of a review item (pending, approved, rejected, resolved) and the user's "
                        "decision and notes.",
    ASK_USER_TOOL: "Ask the user a question and get their answer: a decision you need before going on, a choice "
                   "between options, something only they know. It shows in their footer as `AI asks: ...` "
                   "and they answer with `review answer`. Prefer it to guessing, or to stopping. Give "
                   "`choices` when the answers are few. Returns the answer, or status pending if they have "
                   "not answered yet (poll get_review_status).",
    RESOLVE_TOOL: "Mark a review item as dealt with: a flag you have handled, or a review you have acted on. "
                  "Say what you did in resolution_notes.",
}
# The co-pilot tools: questions to the interactive shell over its private link (shellcraft.mainlink).
RECENT_OUTPUT_TOOL = "get_recent_output"
JOBS_TOOL = "get_jobs"
JOB_OUTPUT_TOOL = "get_job_output"
HISTORY_TOOL = "get_history"
GET_CLIP_TOOL = "get_clip"
SET_CLIP_TOOL = "set_clip"
OPEN_SUBSHELL_TOOL = "open_subshell"
RUN_TOOL = "run_in_shell"
GRANT_TOOL = "request_session_grant"
EVENTS_TOOL = "get_events"
SEARCH_TOOL = "search_output"
SHELL_TOOLS = (RECENT_OUTPUT_TOOL, JOBS_TOOL, JOB_OUTPUT_TOOL, HISTORY_TOOL, GET_CLIP_TOOL, SET_CLIP_TOOL,
               OPEN_SUBSHELL_TOOL, RUN_TOOL, GRANT_TOOL, EVENTS_TOOL, SEARCH_TOOL)
MAX_BRIEF = 64 * 1024
LINK_TIMEOUT = 5.0  # seconds to wait for the shell's answer
NO_MAIN_MESSAGE = ("no interactive ShellCraft is running on this machine (start `shellcraft`; it publishes "
                   "~/.shellcraft/mainlink.json for this server to find)")
_NO_MAIN = object()
SHELL_SCHEMAS: dict[str, dict[str, Any]] = {
    RECENT_OUTPUT_TOOL: {
        "type": "object",
        "properties": {
            "lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200,
                      "description": "How many of the last lines of the user's screen to return."},
            "strip_ansi": {"type": "boolean", "default": True, "description": "Drop the colors; false keeps them."},
        },
        "additionalProperties": False,
    },
    JOBS_TOOL: {"type": "object", "properties": {}, "additionalProperties": False},
    JOB_OUTPUT_TOOL: {
        "type": "object",
        "properties": {
            "number": {"type": "integer", "minimum": 1, "description": "The subshell number the footer shows."},
            "lines": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200},
            "strip_ansi": {"type": "boolean", "default": True},
        },
        "required": ["number"],
        "additionalProperties": False,
    },
    HISTORY_TOOL: {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}},
        "additionalProperties": False,
    },
    GET_CLIP_TOOL: {"type": "object", "properties": {}, "additionalProperties": False},
    SET_CLIP_TOOL: {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 65536,
                                "description": "What Alt-v pastes at the user's prompt: usually one command line."}},
        "required": ["text"],
        "additionalProperties": False,
    },
    OPEN_SUBSHELL_TOOL: {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 40,
                     "description": "A short label for the footer, e.g. \"auth.log malware\"."},
            "title": {"type": "string", "maxLength": 120, "description": "The headline printed first (default: name)."},
            "text": {"type": "string", "maxLength": 65536,
                     "description": "What the subshell prints at start: your findings, the evidence, suggested next "
                                    "commands. Plain text or ANSI. Keep it under ~60 lines."},
            "alert_level": {"type": "string", "enum": ["info", "warn", "crit"], "default": "info",
                            "description": "The alert that points to it: info (good to know), warn (look soon), "
                                           "crit (act now; stays until dismissed)."},
            "cwd": {"type": "string", "description": "The folder the subshell starts in (default: the user's)."},
        },
        "required": ["name", "text"],
        "additionalProperties": False,
    },
    RUN_TOOL: {
        "type": "object",
        "properties": {
            "command": {"type": "string", "maxLength": 4000,
                        "description": "One ShellCraft command line, exactly as the user would type it."},
            "reason": {"type": "string", "maxLength": 2000,
                       "description": "Why, what it touches, what could go wrong. Shown to the user when they "
                                      "have to approve it."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
            "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 600, "default": 120,
                             "description": "How long to wait for the command to finish, or for the user to "
                                            "approve it; 0 returns at once (then get_job_output or "
                                            "get_review_status)."},
        },
        "required": ["command", "reason"],
        "additionalProperties": False,
    },
    EVENTS_TOOL: {
        "type": "object",
        "properties": {
            "since": {"type": "integer", "minimum": 0, "default": 0,
                      "description": "The cursor from your last call (0 the first time): only newer events come back."},
            "kinds": {"type": "array", "items": {"type": "string", "enum": ["command", "job", "interrupt"]},
                      "description": "Only these kinds (default: all)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 120, "default": 0,
                             "description": "Hold the call until something new happens, up to this long. Use it "
                                            "instead of polling."},
            "output_lines": {"type": "integer", "minimum": 0, "maximum": 200, "default": 0,
                             "description": "Add the last N lines each command printed."},
        },
        "additionalProperties": False,
    },
    SEARCH_TOOL: {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "maxLength": 500, "description": "A regular expression, case-insensitive."},
            "context": {"type": "integer", "minimum": 0, "maximum": 10, "default": 0,
                        "description": "Lines to add around each match."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "strip_ansi": {"type": "boolean", "default": True},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    GRANT_TOOL: {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "maxLength": 2000,
                       "description": "What you are going to do this session and why you need to run commands "
                                      "without asking each time."},
            "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 600, "default": 120},
        },
        "required": ["reason"],
        "additionalProperties": False,
    },
}
SHELL_DESCRIPTIONS = {
    RECENT_OUTPUT_TOOL: "The last lines of the user's ShellCraft screen: the commands they ran (as `❯ cmd`) and "
                        "what those printed. Needs an interactive ShellCraft running on this machine.",
    JOBS_TOOL: "The user's subshells and background jobs: number, title, state, and origin (\"ai\" for the "
               "ones you opened).",
    JOB_OUTPUT_TOOL: "The last lines a subshell or background job printed, by its number.",
    HISTORY_TOOL: "The last commands the user typed at the prompt (API keys redacted). Use it to understand "
                  "what they are doing; never echo it back to them or store it.",
    GET_CLIP_TOOL: "The user's clip register: what they last copied in ShellCraft ($CLIP).",
    SET_CLIP_TOOL: "Put text in the user's clip register, so Alt-v pastes it at their prompt: the safest way "
                   "to hand them a command. Say so in an alert.",
    OPEN_SUBSHELL_TOOL: "Open a named ShellCraft subshell in the user's terminal that prints your `text` at "
                        "start, and post an alert pointing to it. For findings that deserve their own screen: "
                        "evidence, analysis, next steps. Returns the subshell's number (the user switches with "
                        "a click or Ctrl-] N). Check get_jobs first: don't open a second one for the same finding.",
    RUN_TOOL: "Run a command in the user's interactive ShellCraft, with its trace shown to them. A read-only "
              "command (grep, cat, jq, sort, the query modules; no redirection, no file changes) runs at once; "
              "so does anything while the session grant is on. Otherwise the command waits in the review "
              "queue until the user types `review approve ID`, and the answer carries `mode` \"approval\" "
              "with the item id. The result has the output and exit status. Never use it for anything "
              "destructive without saying so in `reason`.",
    EVENTS_TOOL: "What happened in the user's shell: each command (line, exit status, seconds, folder), each "
                 "job or subshell that ended, each Ctrl-C, numbered. Pass the `cursor` you got back as `since`. "
                 "With wait_seconds the call returns as soon as something new happens, so you do not need to "
                 "poll. The user's own `watch` triggers also put a flag in get_flagged_items when something "
                 "they care about happens.",
    SEARCH_TOOL: "Search what is on the user's screen (their commands and output) for a regular expression. "
                 "Each match says which command printed it. Cheaper than reading 2000 lines of get_recent_output.",
    GRANT_TOOL: "Ask the user once to let you run any command for the rest of this session (`settings ai_run "
                "on`). Do it at the start of a working session with a one-line reason. Returns granted true "
                "or false; the user can revoke it any time.",
}

# Limits on every tool call, so a hung module or a huge result can't take the server down.
CALL_TIMEOUT = float(os.environ.get("SHELLCRAFT_MCP_TIMEOUT", "120"))  # seconds
MAX_STDIN = 10 * 1024 * 1024  # bytes of `stdin` a call may send
MAX_OUTPUT = 2 * 1024 * 1024  # characters of output returned (the rest is cut, with a note)
TOKEN_ENV = "SHELLCRAFT_MCP_TOKEN"

MODULE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Command-line arguments, one list item per argument (e.g. [\"-i\", \"error\"]).",
        },
        "stdin": {
            "type": "string",
            "description": "Text piped into the tool, as if from `previous | tool`. Omit or leave empty for none.",
        },
    },
    "additionalProperties": False,
}

PIPELINE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "A ShellCraft command line such as `cat data.csv | grep -i error` or `curl -s URL | jq -s`.",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

SERVER_INSTRUCTIONS = (
    "ShellCraft tools are Unix-style text filters: each takes its parameters and optional "
    "`stdin` text and returns text. Chain them yourself by passing one tool's output as the next "
    f"tool's `stdin`, or call `{PIPELINE_TOOL}` with a full `a | b` command line. Each command's "
    f"manual is a resource at {MAN_URI}<name>.\n\n"
    f"Review loop: the user flags output for you with `mark`; read it with `{GET_FLAGGED_TOOL}` or the "
    f"{REVIEW_QUEUE_URI} resource, and call `{RESOLVE_TOOL}` when done. Before any destructive or "
    f"irreversible action, call `{REQUEST_REVIEW_TOOL}` and go ahead only if the user approves.\n\n"
    f"Co-pilot: the user's interactive ShellCraft publishes a private link; `{RECENT_OUTPUT_TOOL}`, "
    f"`{JOBS_TOOL}`, `{JOB_OUTPUT_TOOL}`, `{HISTORY_TOOL}` and `{GET_CLIP_TOOL}` read it. Bring findings "
    f"to the user with `{ALERT_TOOL}` (one line), `{OPEN_SUBSHELL_TOOL}` (a named subshell that prints "
    f"your text: anything longer), or `{SET_CLIP_TOOL}` (a command they paste with Alt-v). Run in their "
    f"shell only through `{RUN_TOOL}`: read-only commands run at once, everything does with the session "
    f"grant (`{GRANT_TOOL}`, asked once), the rest waits for `review approve`. Alert levels: info = good "
    "to know, warn = look soon, crit = act now (stays until dismissed)."
)


def _pipeline_description(registry: ModuleRegistry, names: list[str] | None = None) -> str:
    modules = "\n".join(f"  {n}: {registry.get(n).summary}" for n in (registry.names() if names is None else names))
    return (
        "Run a ShellCraft pipeline: commands joined by `|`, each receiving the previous command's "
        "output as stdin. Use it to combine several tools in one call. Inside a pipeline, modules "
        "take CLI-style switches (see each module's usage). File redirection (`>`, `>>`), "
        "file-changing commands (tee, cp, mv, rm, mkdir, touch), env, and shell-state commands "
        "(cd, theme, settings, exit) are disabled. Run `man <command>` inside a pipeline for a command's manual.\n\n"
        "The command line may also be a small script, in bash syntax: `a ; b`, `a && b || c`, "
        "`for h in $(cat hosts.txt); do queryDns $h; done`, `for n in {1..5}; do …; done`, "
        "`if [ \"$x\" = a ]; then …; elif [[ $x == *.gov ]]; then …; else …; fi` (with `test`, `[`, `[[`). "
        "Or with `-py`, where variables are never split and conditions are Python: "
        "`for -py ip in (cat ips.txt) { if (ip.startswith(\"10.\")) { … } else { … } }` "
        "(see `man for` and `man if`). A failing statement in a script shows as a `✗ name: message` "
        "line and the script goes on.\n\n"
        f"Modules:\n{modules}\n\nBuilt-in commands: {', '.join(_usable_builtins())}"
    )


def _usable_builtins() -> list[str]:
    return sorted(n for n, b in BUILTINS.items() if not (b.stateful or b.writes or b.sensitive))


class _Server(Server):
    """Always advertises listChanged for tools and resources, whichever transport asks."""

    def create_initialization_options(self, notification_options=None, *args, **kwargs):
        notification_options = notification_options or NotificationOptions(tools_changed=True,
                                                                            resources_changed=True)
        return super().create_initialization_options(notification_options, *args, **kwargs)


class ShellcraftMCP:
    """The MCP server plus what it needs to tell connected clients that the modules changed."""

    def __init__(self, registry: ModuleRegistry, allow_system: bool = False,
                 expose: Iterable[str] | None = None, timeout: float | None = None, role: str | None = None):
        self.registry = registry
        self.role = role or None  # pinned with --role: this server offers, and allows, only what the role may use
        self.module_calls = 0  # against [budget] module_calls of the policy file
        # Which modules clients may use: those whose .skill doesn't say `expose = false`, narrowed
        # further by --expose NAME,... when given. The pipeline tool follows the same rule.
        self.expose_only = None if expose is None else set(expose)
        self.timeout = CALL_TIMEOUT if timeout is None else timeout
        self.ctx = ShellContext(registry=registry, allow_redirect=False, allow_system=allow_system,
                                allow_stateful=False, allow_writes=False, allow_sensitive=False,
                                allow_aliases=False, module_filter=self.exposed)
        # Clients on the 2026-07-28+ protocol hear about changes through subscriptions/listen;
        # earlier clients through their connection, which we remember from their requests.
        self.bus = InMemorySubscriptionBus()
        self.connections: weakref.WeakSet[Any] = weakref.WeakSet()
        self.server = _Server(
            "shellcraft",
            version=__version__,
            title="ShellCraft",
            instructions=SERVER_INSTRUCTIONS,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
            on_subscriptions_listen=ListenHandler(self.bus),
        )

    def exposed(self, name: str) -> bool:
        spec = self.registry.get(name)
        if spec is None or not spec.exposed:
            return False
        return self.expose_only is None or name in self.expose_only

    def tool_names(self) -> list[str]:
        return [n for n in self.registry.names() if self.exposed(n)]

    def _remember(self, request_ctx: Any) -> None:
        # The per-request ServerSession is thrown away after each request; the Connection behind it
        # lives as long as the client stays connected. mcp 2.x has no public accessor for it here.
        connection = getattr(request_ctx, "connection", None) or getattr(
            getattr(request_ctx, "session", None), "_connection", None)
        if connection is not None and hasattr(connection, "send_tool_list_changed"):
            try:
                self.connections.add(connection)
            except TypeError:  # not weak-referenceable: this client won't get legacy notifications
                pass

    # ── tools ────────────────────────────────────────────────────────────────

    async def _list_tools(self, request_ctx, _params) -> types.ListToolsResult:
        self._remember(request_ctx)
        tools = [
            types.Tool(name=spec.name, description=spec.description,
                       input_schema=spec.input_schema or MODULE_INPUT_SCHEMA)
            for spec in (self.registry.get(n) for n in self.tool_names())
        ]
        tools.append(types.Tool(name=PIPELINE_TOOL, description=_pipeline_description(self.registry, self.tool_names()),
                                input_schema=PIPELINE_INPUT_SCHEMA))
        tools.append(types.Tool(name=ALERT_TOOL, description=ALERT_DESCRIPTION, input_schema=ALERT_INPUT_SCHEMA))
        for name, schema in ((GET_FLAGGED_TOOL, GET_FLAGGED_SCHEMA), (REQUEST_REVIEW_TOOL, REQUEST_REVIEW_SCHEMA),
                             (REVIEW_STATUS_TOOL, GET_REVIEW_STATUS_SCHEMA), (RESOLVE_TOOL, RESOLVE_SCHEMA),
                             (ASK_USER_TOOL, ASK_USER_SCHEMA)):
            tools.append(types.Tool(name=name, description=REVIEW_DESCRIPTIONS[name], input_schema=schema))
        for name in SHELL_TOOLS:
            tools.append(types.Tool(name=name, description=SHELL_DESCRIPTIONS[name], input_schema=SHELL_SCHEMAS[name]))
        for name in TASK_TOOLS:
            tools.append(types.Tool(name=name, description=TASK_DESCRIPTIONS[name], input_schema=TASK_SCHEMAS[name]))
        for name in FINDING_TOOLS:
            tools.append(types.Tool(name=name, description=FINDING_DESCRIPTIONS[name],
                                    input_schema=FINDING_SCHEMAS[name]))
        from shellcraft import ai_policy

        policy = ai_policy.load()
        tools = [t for t in tools if ai_policy.role_allows_tool(policy, self.role, t.name)]
        modules = set(self.tool_names())
        for tool in tools:  # every tool takes the caller's label (the footer, the task board and the audit log show it)
            schema = dict(tool.input_schema)
            props = dict(schema.get("properties") or {})
            props.setdefault("agent", AGENT_PROPERTY)
            if tool.name in modules:  # a module: its answer may come from the memory
                props.setdefault("fresh", FRESH_PROPERTY)
            schema["properties"] = props
            tool.input_schema = schema
        return types.ListToolsResult(tools=tools)

    async def _call_tool(self, request_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        """Every tool call: the role and the budget are checked, the call is made, and it is written
        to the audit log (`ai log`)."""
        import time

        from shellcraft import ai_audit, ai_policy

        self._remember(request_ctx)
        arguments = dict(params.arguments or {})
        agent = " ".join(str(arguments.pop("agent", "") or "").split())[:40]
        client = _client_name(request_ctx)
        who = f"{client} · {agent}" if agent else client
        policy = ai_policy.load()
        started = time.monotonic()
        if not ai_policy.role_allows_tool(policy, self.role, params.name):
            result = _error(f"{params.name}: the {self.role} role may not use this tool")
        elif self._over_budget(policy, params.name):
            result = _error(f"{params.name}: the budget of {policy.budget} module calls for this server is used up "
                            f"(`[budget]` in {ai_policy.FILE})")
        else:
            result = await self._dispatch_tool(request_ctx, params, arguments, who, agent)
        summary = str(arguments.get("command") or arguments.get("title") or arguments.get("message")
                      or arguments.get("args") or arguments.get("reason") or "")
        await anyio.to_thread.run_sync(lambda: ai_audit.record(
            params.name, summary, agent=agent, role=self.role or "", client=client,
            tier=_tier_of(params.name, result), ok=not result.is_error, seconds=time.monotonic() - started))
        return result

    def _over_budget(self, policy: Any, name: str) -> bool:
        if name not in self.tool_names() and name != PIPELINE_TOOL:
            return False
        if policy.budget is None:
            return False
        self.module_calls += 1
        return self.module_calls > policy.budget

    async def _dispatch_tool(self, request_ctx, params: types.CallToolRequestParams, arguments: dict[str, Any],
                             who: str, agent: str) -> types.CallToolResult:
        if params.name == ALERT_TOOL:
            return await _call(lambda: _send_alert(arguments, who), self.timeout)
        if params.name in REVIEW_TOOLS:
            return await self._review_tool(params.name, arguments, who)
        if params.name in SHELL_TOOLS:
            return await self._shell_tool(params.name, arguments, who, agent)
        if params.name in TASK_TOOLS:
            return await self._task_tool(params.name, arguments, who, agent)
        if params.name in FINDING_TOOLS:
            return await self._finding_tool(params.name, arguments, agent)
        if params.name == PIPELINE_TOOL:
            command = str(arguments.get("command", ""))
            if len(command) > MAX_STDIN:
                return _error(f"the command line is too long (over {MAX_STDIN} characters)")
            return await _call(lambda: _run_pipeline(command, self.ctx), self.timeout)

        spec = self.registry.get(params.name)
        if spec is None or not self.exposed(params.name):
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}")
        fresh = bool(arguments.pop("fresh", False))
        stdin = arguments.pop("stdin", None)
        stdin = "" if stdin is None else str(stdin)
        if len(stdin) > MAX_STDIN:
            return _error(f"stdin is too large (over {MAX_STDIN} characters)")
        if spec.input_schema is not None:
            try:
                args = spec.argv(arguments)
            except ParamError as exc:
                return _error(f"{spec.name}: {exc}")
        else:
            raw_args = arguments.get("args") or []
            if not isinstance(raw_args, list):
                return _error("`args` must be an array of strings")
            args = [str(a) for a in raw_args]
        return await self._run_module(spec, args, stdin, fresh)

    async def _run_module(self, spec: Any, args: list[str], stdin: str, fresh: bool) -> types.CallToolResult:
        """Run a module, or answer from the memory of the last few minutes (shellcraft.modcache): two
        agents asking the same question do not spend two API calls. Errors are never remembered."""
        from shellcraft import ai_policy, modcache

        policy = ai_policy.load()
        ttl = 0.0 if fresh or spec.name in policy.cache_exclude else policy.cache_seconds
        key = modcache.key(spec.name, args, stdin, modcache.version_of(spec.path)) if ttl else ""
        if ttl:
            hit = await anyio.to_thread.run_sync(lambda: modcache.get(key, ttl))
            if hit is not None:
                self.module_calls = max(0, self.module_calls - 1)  # it spent no API call
                return types.CallToolResult(content=[types.TextContent(type="text", text=hit[0])],
                                            meta={"shellcraft": {"cached": True, "age_seconds": round(hit[1], 1)}})
        result = await _call(lambda: to_text(spec.run(args, stdin)), self.timeout)
        if ttl and not result.is_error and result.content and isinstance(result.content[0], types.TextContent):
            await anyio.to_thread.run_sync(lambda: modcache.put(key, spec.name, result.content[0].text, ttl))
        return result

    # ── the co-pilot: questions to the interactive shell (shellcraft.mainlink) ─

    async def _shell_tool(self, name: str, arguments: dict[str, Any], client: str,
                          agent: str = "") -> types.CallToolResult:
        from shellcraft import mainlink

        by = f"mcp · {client}"
        if name == GRANT_TOOL:
            return await self._request_grant(arguments, by)
        try:
            request = link_request(name, arguments, by, self.role, agent)
        except ValueError as exc:
            return _error(f"{name}: {exc}")
        timeout = LINK_TIMEOUT + (request[1].get("wait", 0) if name == RUN_TOOL else
                                  request[4] if name == EVENTS_TOOL else 0)
        answer = await anyio.to_thread.run_sync(lambda: mainlink.ask(request, _NO_MAIN, timeout=timeout))
        if answer is _NO_MAIN:
            return _error(NO_MAIN_MESSAGE)
        if isinstance(answer, tuple) and answer[:1] == ("error",):
            return _error(f"{name}: {answer[1]}")
        if name == SET_CLIP_TOOL:
            return _json_result({"set": True, "characters": len(request[1])})
        if name == RUN_TOOL and isinstance(answer, dict) and answer.get("denied"):
            return _error(f"{name}: not run: {answer.get('reason') or 'the policy does not allow it'}")
        if name == RUN_TOOL and isinstance(answer, dict) and answer.get("needs_approval"):
            return await self._run_with_approval(arguments, by, answer.get("reason") or "")
        if name == GET_CLIP_TOOL:
            return _json_result({"text": answer})
        return _json_result(answer)

    async def _run_with_approval(self, arguments: dict[str, Any], by: str, why: str = "") -> types.CallToolResult:
        """`run_in_shell` for a command the shell would not run on its own: a review item of type
        "command"; `review approve ID` runs it there and stores the output with the decision."""
        from shellcraft import review_spool as spool

        command, reason = str(arguments.get("command", "")).strip(), str(arguments.get("reason", ""))
        if why:
            reason = f"{reason}\n\n({why})"
        priority = str(arguments.get("priority") or "medium")
        try:
            item = await anyio.to_thread.run_sync(lambda: spool.create(
                "review", command[:200], reason, [spool.artifact("command", command, "text")], priority, "command",
                created_by=by))
            item = await self._await_decision(item, arguments.get("wait_seconds", 120))
        except (spool.SpoolError, ValueError) as exc:
            return _error(f"{RUN_TOOL}: {exc}")
        mode = {"approved": "approved", "rejected": "rejected"}.get(item["status"], "approval")
        return _json_result({**_status_of(item), "mode": mode, "command": command, "result": item.get("result"),
                             "note": None if mode != "approval" else
                             "waiting for `review approve` in the user's shell; poll get_review_status"})

    async def _request_grant(self, arguments: dict[str, Any], by: str) -> types.CallToolResult:
        from shellcraft import mainlink
        from shellcraft import review_spool as spool

        reason = str(arguments.get("reason", "")).strip()
        if not reason:
            return _error(f"{GRANT_TOOL}: `reason` is required")
        state = await anyio.to_thread.run_sync(lambda: mainlink.ask(("ai-state",), None))
        if isinstance(state, dict) and state.get("on"):
            return _json_result({"granted": True, "status": "approved", "item_id": None,
                                 "note": "the user's ai_access setting is on: commands already run at once"})
        try:
            item = await anyio.to_thread.run_sync(lambda: spool.create(
                "review", "run any command this session", reason, [], "medium", "grant", created_by=by))
            item = await self._await_decision(item, arguments.get("wait_seconds", 120))
        except (spool.SpoolError, ValueError) as exc:
            return _error(f"{GRANT_TOOL}: {exc}")
        return _json_result({**_status_of(item), "granted": item["status"] == "approved"})

    async def _ask_user(self, arguments: dict[str, Any], by: str) -> types.CallToolResult:
        """`ask_user`: a question item; the user answers with `review answer`."""
        from shellcraft import review_spool as spool

        question = " ".join(str(arguments.get("question", "")).split())
        if not question:
            return _error(f"{ASK_USER_TOOL}: `question` is required")
        choices = arguments.get("choices") or []
        if not isinstance(choices, list):
            return _error(f"{ASK_USER_TOOL}: `choices` must be an array of strings")
        about = str(arguments.get("about") or "")

        def make() -> dict[str, Any]:
            parent = spool.find(about)["id"] if about else ""
            made = spool.create("question", question[:200], str(arguments.get("details", "")), [],
                                str(arguments.get("priority") or "medium"), "question", created_by=by,
                                choices=[str(c) for c in choices], parent=parent)
            if parent:
                spool.reply(parent, question, by)
            return made

        item = await anyio.to_thread.run_sync(make)
        item = await self._await_decision(item, arguments.get("wait_seconds", 120))
        return _json_result({**_status_of(item), "answered": item["status"] == "answered",
                             "answer": (item.get("answer") or {}).get("text")})

    async def _await_decision(self, item: dict[str, Any], wait_seconds: Any) -> dict[str, Any]:
        """Poll the item every 0.5 s for up to `wait_seconds` (capped below the call timeout)."""
        from shellcraft import review_spool as spool

        wait = max(0.0, min(float(wait_seconds or 0), 600.0, self.timeout - 5))
        deadline = anyio.current_time() + wait
        while wait and anyio.current_time() < deadline and item["status"] == "pending":
            await anyio.sleep(0.5)
            item = await anyio.to_thread.run_sync(lambda: spool.get(item["id"]))
        return item

    # ── the case file (shellcraft.findings) ───────────────────────────────────

    async def _finding_tool(self, name: str, arguments: dict[str, Any], agent: str) -> types.CallToolResult:
        from shellcraft import findings

        try:
            return _json_result(await anyio.to_thread.run_sync(lambda: _finding_call(name, arguments, agent)))
        except findings.FindingError as exc:
            return _error(f"{name}: {exc}")
        except (TypeError, ValueError) as exc:
            return _error(f"{name}: bad arguments: {exc}")

    # ── the task board (shellcraft.tasks) ─────────────────────────────────────

    async def _task_tool(self, name: str, arguments: dict[str, Any], who: str, agent: str) -> types.CallToolResult:
        from shellcraft import tasks

        owner = agent or who  # who holds a claim: the label if given, else the client
        try:
            return _json_result(await anyio.to_thread.run_sync(lambda: _task_call(name, arguments, who, owner)))
        except tasks.TaskError as exc:
            return _error(f"{name}: {exc}")
        except (TypeError, ValueError) as exc:
            return _error(f"{name}: bad arguments: {exc}")

    # ── the review loop (shellcraft.review_spool) ─────────────────────────────

    async def _review_tool(self, name: str, arguments: dict[str, Any], client: str) -> types.CallToolResult:
        from shellcraft import review_spool as spool

        by = f"mcp · {client}"
        try:
            if name == GET_FLAGGED_TOOL:
                status, kind = arguments.get("status", "pending"), arguments.get("kind", "flag")
                items = await anyio.to_thread.run_sync(lambda: spool.list_items(status, kind))
                if not arguments.get("include_artifacts", True):
                    items = [spool.summary(i) for i in items]
                return _json_result(items)
            if name == REVIEW_STATUS_TOOL:
                item = await anyio.to_thread.run_sync(lambda: spool.get(str(arguments.get("item_id", ""))))
                return _json_result(_status_of(item))
            if name == RESOLVE_TOOL:
                item_id, notes = str(arguments.get("item_id", "")), str(arguments.get("resolution_notes", ""))
                item = await anyio.to_thread.run_sync(lambda: spool.resolve(item_id, notes, by))
                return _json_result(_status_of(item))
            if name == ASK_USER_TOOL:
                return await self._ask_user(arguments, by)
            # request_human_review
            title, description = str(arguments.get("title", "")).strip(), str(arguments.get("description", ""))
            if not title:
                return _error("request_human_review: `title` is required")
            raw = arguments.get("artifacts") or []
            if not isinstance(raw, list):
                return _error("request_human_review: `artifacts` must be an array")
            artifacts = [spool.artifact(str(a.get("name", "text")), str(a.get("content", "")),
                                        str(a.get("kind", "text"))) for a in raw if isinstance(a, dict)]
            priority = str(arguments.get("priority", "medium") or "medium")
            item = await anyio.to_thread.run_sync(
                lambda: spool.create("review", title, description, artifacts, priority, "", created_by=by))
            wait = max(0.0, min(float(arguments.get("wait_seconds", 0) or 0), 600.0, self.timeout - 5))
            deadline = anyio.current_time() + wait
            while wait and anyio.current_time() < deadline:
                await anyio.sleep(0.5)
                item = await anyio.to_thread.run_sync(lambda: spool.get(item["id"]))
                if item["status"] != "pending":
                    break
            return _json_result(_status_of(item))
        except spool.SpoolError as exc:
            return _error(f"{name}: {exc}")
        except (TypeError, ValueError) as exc:
            return _error(f"{name}: bad arguments: {exc}")

    async def notify_reviews_changed(self, uri: str = REVIEW_QUEUE_URI) -> None:
        """A spool changed (the review queue, or the task board): its resource is to be read again."""
        await self.bus.publish(ResourcesListChanged())
        await self.bus.publish(ResourceUpdated(uri))
        for connection in list(self.connections):
            try:
                await connection.send_resource_list_changed()
                send_updated = getattr(connection, "send_resource_updated", None)
                if send_updated is not None:
                    await send_updated(uri)
            except Exception:  # noqa: BLE001 — a closed connection just stops getting notified
                self.connections.discard(connection)

    async def watch_reviews(self, interval: float = REVIEW_INTERVAL) -> None:
        """Look at the review spool every `interval`: a new or changed item (the shell's `mark`, the
        user's answer, another client's request) is announced to the connected clients."""
        from shellcraft import review_spool as spool
        from shellcraft import tasks

        last = await anyio.to_thread.run_sync(spool.signature)
        last_tasks = await anyio.to_thread.run_sync(tasks.signature)
        while True:
            await anyio.sleep(interval)
            now = await anyio.to_thread.run_sync(spool.signature)
            if now != last:
                last = now
                await self.notify_reviews_changed()
            board = await anyio.to_thread.run_sync(tasks.signature)
            if board != last_tasks:
                last_tasks = board
                await self.notify_reviews_changed(TASKS_URI)

    # ── resources: man pages ─────────────────────────────────────────────────

    def _manual_names(self) -> list[str]:
        modules = [n for n in self.tool_names() if self.registry.get(n).doc_md]
        return modules + [n for n in _usable_builtins() if n not in self.registry.modules]

    async def _list_resources(self, request_ctx, _params) -> types.ListResourcesResult:
        self._remember(request_ctx)
        resources = []
        for name in self._manual_names():
            spec = self.registry.get(name)
            summary = spec.summary if spec else BUILTINS[name].summary
            kind = "module" if spec else "built-in command"
            resources.append(types.Resource(uri=MAN_URI + name, name=f"man {name}", title=f"{name} manual",
                                            description=f"Manual for the {kind} `{name}`: {summary}",
                                            mime_type="text/markdown"))
        resources.append(types.Resource(uri=REVIEW_QUEUE_URI, name="review queue", title="Review queue",
                                        description="What is pending in ShellCraft's review spool: items the user "
                                                    "flagged for you (`mark`) and review requests waiting for "
                                                    "an answer. JSON; read the full items with get_flagged_items.",
                                        mime_type="application/json"))
        resources.append(types.Resource(uri=TASKS_URI, name="task board", title="Task board",
                                        description="The tasks on the shared board that are still open, claimed, "
                                                    "working or blocked. JSON; claim_task and update_task change "
                                                    "them.", mime_type="application/json"))
        return types.ListResourcesResult(resources=resources)

    async def _read_resource(self, request_ctx, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        self._remember(request_ctx)
        uri = str(params.uri)
        if uri == REVIEW_QUEUE_URI:
            from shellcraft import review_spool as spool

            items = await anyio.to_thread.run_sync(lambda: spool.list_items("pending"))
            text = json.dumps([spool.summary(i) for i in items], ensure_ascii=False, indent=1)
            return types.ReadResourceResult(
                contents=[types.TextResourceContents(uri=uri, mime_type="application/json", text=text)])
        if uri == TASKS_URI:
            from shellcraft import tasks

            board = await anyio.to_thread.run_sync(lambda: tasks.list_tasks("active"))
            text = json.dumps([{k: v for k, v in t.items() if k not in ("notes", "description")} for t in board],
                              ensure_ascii=False, indent=1)
            return types.ReadResourceResult(
                contents=[types.TextResourceContents(uri=uri, mime_type="application/json", text=text)])
        name = uri[len(MAN_URI):] if uri.startswith(MAN_URI) else ""
        if name not in self._manual_names():
            raise MCPError(types.INVALID_PARAMS, f"Unknown resource: {uri}")
        try:
            text = manual_text(self.registry, name)
        except CommandError as exc:
            raise MCPError(types.INVALID_PARAMS, str(exc)) from None
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, mime_type="text/markdown", text=text)])

    # ── hot reload ───────────────────────────────────────────────────────────

    async def notify_changed(self) -> None:
        """Tell every connected client that the tool and resource lists changed."""
        await self.bus.publish(ToolsListChanged())
        await self.bus.publish(ResourcesListChanged())
        for connection in list(self.connections):
            try:
                await connection.send_tool_list_changed()
                await connection.send_resource_list_changed()
            except Exception:  # noqa: BLE001 — a closed connection just stops getting notified
                self.connections.discard(connection)

    async def reload_if_changed(self, watcher: ModuleWatcher) -> list[str]:
        changed = watcher.changes()
        if changed:
            self.registry.reload(changed)
            log.info("modules changed (%s): reloaded %d module(s)", ", ".join(changed), len(self.registry))
            for warning in self.registry.warnings:
                log.warning(warning)
            await self.notify_changed()
        return changed

    async def watch(self, interval: float = WATCH_INTERVAL) -> None:
        watcher = ModuleWatcher(*self.registry.directories)
        while True:
            await anyio.sleep(interval)
            await self.reload_if_changed(watcher)


def build_server(registry: ModuleRegistry, allow_system: bool = False, expose: Iterable[str] | None = None,
                 timeout: float | None = None, role: str | None = None) -> Server:
    return ShellcraftMCP(registry, allow_system, expose, timeout, role).server


def _run_pipeline(command: str, ctx: ShellContext) -> str:
    result = run_line(command, ctx)
    return "" if result is None else to_text(result.output)


async def _call(fn: Callable[[], str], timeout: float = CALL_TIMEOUT) -> types.CallToolResult:
    import threading

    from shellcraft.modkit import run_cancellable

    cancel = threading.Event()
    try:
        with anyio.fail_after(timeout):
            # abandon_on_cancel: a timed-out call returns an error now; its thread is told to stop
            # (modkit.cancelled()) and otherwise finishes on its own.
            text = await anyio.to_thread.run_sync(lambda: run_cancellable(fn, cancel), abandon_on_cancel=True)
    except TimeoutError:
        cancel.set()
        return _error(f"the call did not finish within {timeout:.0f} s and was abandoned")
    except ParseError as exc:
        return _error(f"parse error: {exc.message}")
    except PipelineError as exc:
        return _error(f"{exc.name} (segment {exc.index}/{exc.total}): {exc.message}")
    except ModuleError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 — tool failures go back to the model, not the transport
        log.exception("tool call failed")
        return _error(f"{type(exc).__name__}: {exc}")
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n… [output cut after {MAX_OUTPUT} characters]\n"
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def _finding_call(name: str, arguments: dict[str, Any], agent: str) -> Any:
    """One case-file tool call (runs in a thread: the case is files)."""
    from shellcraft import findings

    def text(key: str) -> str | None:
        value = arguments.get(key)
        return None if value is None else str(value)

    def listing(key: str) -> list[Any] | None:
        value = arguments.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"`{key}` must be an array")
        return value

    if name == ADD_FINDING_TOOL:
        item, created = findings.add(
            text("kind") or "", text("value") or "", text("title") or "", text("summary") or "", listing("evidence"),
            text("confidence") or "medium", text("severity") or "info", agent, text("task_id") or "",
            listing("tags"), text("case"))
        return {**item, "created": created}
    if name == LIST_FINDINGS_TOOL:
        found = findings.list_findings(text("case"), text("kind"), text("severity"), text("tag"), text("query"),
                                       text("status"), text("agent"))
        return [{k: v for k, v in f.items() if k != "evidence"} | {"evidence": len(f.get("evidence", []))}
                for f in found]
    if name == GET_FINDING_TOOL:
        return findings.find(text("finding_id") or "")
    if name == UPDATE_FINDING_TOOL:
        item = findings.find(text("finding_id") or "")
        return findings.update(item["id"], text("title"), text("summary"), text("severity"), text("confidence"),
                               text("status"), listing("add_tags"), listing("remove_tags"), listing("evidence"),
                               agent, item["case"])
    raise ValueError(f"unknown tool {name}")


def _task_call(name: str, arguments: dict[str, Any], who: str, owner: str) -> Any:
    """One task-board tool call (runs in a thread: the board is files)."""
    from shellcraft import tasks

    def text(key: str) -> str:
        value = arguments.get(key)
        return "" if value is None else str(value)

    if name == CREATE_TASK_TOOL:
        depends = arguments.get("depends_on") or []
        if not isinstance(depends, list):
            raise ValueError("`depends_on` must be an array of task ids")
        return tasks.create(text("title"), text("description"), text("parent"), [str(d) for d in depends],
                            text("source"), text("priority") or "medium", created_by=f"mcp · {who}")
    if name == LIST_TASKS_TOOL:
        if text("task_id"):
            return tasks.find(text("task_id"))
        listed = tasks.list_tasks(text("status") or "active", text("owner") or None, text("parent") or None,
                                  bool(arguments.get("claimable")))
        return [{k: v for k, v in t.items() if k not in ("notes", "description")} | {"notes": len(t["notes"])}
                for t in listed]
    if name == CLAIM_TASK_TOOL:
        return tasks.claim(text("task_id") or None, owner, arguments.get("lease_seconds", tasks.LEASE))
    if name == UPDATE_TASK_TOOL:
        return tasks.update(text("task_id"), owner, text("status") or None, text("note"),
                            arguments.get("result"), arguments.get("lease_seconds", tasks.LEASE))
    if name == RELEASE_TASK_TOOL:
        return tasks.release(text("task_id"), owner, text("note"))
    raise ValueError(f"unknown tool {name}")


def _tier_of(tool: str, result: types.CallToolResult) -> str:
    """For the audit log: how run_in_shell was answered (read-only, granted, approval, denied)."""
    if tool != RUN_TOOL:
        return ""
    try:
        return str(json.loads(result.content[0].text).get("mode") or "")
    except (AttributeError, IndexError, ValueError, TypeError):
        return "denied" if result.is_error and "not run" in str(getattr(result.content[0], "text", "")) else ""


def _client_name(request_ctx: Any) -> str:
    """The connected client's name from its initialize request (`claude-code`, `Claude Desktop`…)."""
    try:
        info = request_ctx.session.client_params.client_info
        return str(info.name or "").strip() or "mcp client"
    except AttributeError:
        return "mcp client"


def _send_alert(arguments: dict[str, Any], client: str = "mcp client") -> str:
    """The send_tui_alert tool: drop the alert into the running shell's spool."""
    from shellcraft import alerts

    message = str(arguments.get("message", "")).strip()
    if not message:
        raise CommandError("send_tui_alert: `message` is required")
    severity = alerts.normalize_level(arguments.get("severity", "info") or "info")
    if severity is None:
        raise CommandError(f"send_tui_alert: severity must be one of {', '.join(alerts.LEVELS)}")
    timeout: Any = arguments.get("timeout_seconds")
    if timeout is not None:
        try:
            timeout = max(1.0, float(timeout))
        except (TypeError, ValueError):
            raise CommandError("send_tui_alert: timeout_seconds must be a number") from None
    else:
        timeout = "default"
    content = arguments.get("content", "")
    if content is not None and not isinstance(content, str):
        raise CommandError("send_tui_alert: content must be a string")
    try:
        alerts.send(message, severity, timeout, source=f"mcp · {client}", content=content or "")
    except OSError as exc:
        raise CommandError(f"send_tui_alert: {exc}") from None
    return f"alert shown ({severity}): {message}" + (" (with content)" if content else "")


def link_request(name: str, arguments: dict[str, Any], by: str, role: str | None = None, agent: str = "") -> tuple:
    """The request tuple a co-pilot tool sends over the link (shellcraft.mainlink); ValueError
    for arguments that don't fit."""
    def integer(key: str, default: int, low: int, high: int) -> int:
        value = arguments.get(key, default)
        if value is None:
            value = default
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"`{key}` must be an integer from {low} to {high}")
        return value

    strip = bool(arguments.get("strip_ansi", True))
    if name == RECENT_OUTPUT_TOOL:
        return ("scrollback", integer("lines", 200, 1, 2000), strip)
    if name == JOBS_TOOL:
        return ("jobs",)
    if name == JOB_OUTPUT_TOOL:
        return ("job-output", integer("number", 0, 1, 10**9), integer("lines", 200, 1, 5000), strip)
    if name == HISTORY_TOOL:
        return ("history", integer("n", 50, 1, 500))
    if name == GET_CLIP_TOOL:
        return ("clip-get", 0)
    if name == SET_CLIP_TOOL:
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("`text` is required")
        if len(text) > 65536:
            raise ValueError("`text` is over 64 KB")
        return ("clip-set", text)
    if name == OPEN_SUBSHELL_TOOL:
        subname, text = str(arguments.get("name") or "").strip(), arguments.get("text")
        if not subname:
            raise ValueError("`name` is required")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("`text` is required")
        if len(text) > MAX_BRIEF:
            raise ValueError("`text` is over 64 KB")
        level = str(arguments.get("alert_level") or "info")
        if level not in ("info", "warn", "crit"):
            raise ValueError("`alert_level` must be info, warn or crit")
        spec = {"name": subname[:40], "title": str(arguments.get("title") or subname)[:120], "text": text,
                "source": by, "level": level}
        if isinstance(arguments.get("cwd"), str):
            spec["cwd"] = arguments["cwd"]
        return ("open-sub", spec)
    if name == EVENTS_TOOL:
        kinds = arguments.get("kinds")
        if kinds is not None and (not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds)):
            raise ValueError("`kinds` must be a list of strings")
        return ("events", integer("since", 0, 0, 10**12), kinds, integer("limit", 100, 1, 500),
                integer("wait_seconds", 0, 0, 120), integer("output_lines", 0, 0, 200))
    if name == SEARCH_TOOL:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("`pattern` is required")
        if len(pattern) > 500:
            raise ValueError("`pattern` is over 500 characters")
        return ("search-output", pattern, integer("context", 0, 0, 10), integer("limit", 50, 1, 200), strip)
    if name == RUN_TOOL:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("`command` is required")
        if len(command) > 4000:
            raise ValueError("`command` is over 4000 characters")
        if not str(arguments.get("reason", "")).strip():
            raise ValueError("`reason` is required")
        return ("run", {"command": command.strip(), "reason": str(arguments["reason"]), "source": by,
                        "wait": integer("wait_seconds", 120, 0, 600), "role": role or "", "agent": agent})
    raise ValueError(f"unknown tool {name}")


def _status_of(item: dict[str, Any]) -> dict[str, Any]:
    out = {"item_id": item["id"], "kind": item["kind"], "status": item["status"],
           "decision": item.get("decision"), "resolution": item.get("resolution")}
    if item.get("kind") == "question":
        out.update(question=item.get("title"), choices=item.get("choices") or [], answer=item.get("answer"))
    if item.get("thread"):
        out["thread"] = item["thread"]
    if item.get("type"):
        out["type"] = item["type"]
    if item.get("result") is not None:
        out["result"] = item["result"]
    return out


def _json_result(value: Any) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text",
                                                           text=json.dumps(value, ensure_ascii=False, indent=1))])


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


# ── transports ───────────────────────────────────────────────────────────────

class AddressError(ValueError):
    pass


def parse_http_address(value: str) -> tuple[str, int]:
    """'127.0.0.1:38765', 'localhost:38765', '[::1]:38765' or ':38765' → (host, port); loopback only."""
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        raise AddressError(f"invalid address '{value}' (expected HOST:PORT, e.g. 127.0.0.1:38765)")
    host = host.strip("[]") or "127.0.0.1"
    if host != "localhost":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise AddressError(f"refusing to listen on {host}: the MCP HTTP server only binds to this "
                               "machine (127.0.0.1, localhost or ::1), because connected clients can read "
                               "your files")
    return host, int(port)


def _log_startup(app: ShellcraftMCP, watch: bool) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="shellcraft-mcp: %(message)s")
    for warning in app.registry.warnings:
        log.warning(warning)
    log.info("serving %d module(s): %s", len(app.registry), ", ".join(app.registry.names()) or "none")
    if watch:
        log.info("hot reload on: watching %s", ", ".join(str(d) for d in app.registry.directories))


def serve(registry: ModuleRegistry, allow_system: bool = False, watch: bool = True,
          expose: Iterable[str] | None = None, role: str | None = None) -> None:
    """Serve over stdio."""
    app = ShellcraftMCP(registry, allow_system, expose, role=role)
    _log_startup(app, watch)

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            if watch:
                tg.start_soon(app.watch)
            tg.start_soon(app.watch_reviews)  # the review spool, whatever hot_reload says
            async with stdio_server() as (read_stream, write_stream):
                await app.server.run(read_stream, write_stream, app.server.create_initialization_options())
            tg.cancel_scope.cancel()

    anyio.run(main)


def resolve_token(token: str | None, no_token: bool = False) -> str | None:
    """The bearer token the HTTP transport requires: the one given, else $SHELLCRAFT_MCP_TOKEN,
    else a fresh random one. None only when `no_token` asks for an open server."""
    if no_token:
        return None
    return token or os.environ.get(TOKEN_ENV) or secrets.token_urlsafe(24)


def require_bearer(asgi: Any, token: str) -> Any:
    """ASGI middleware: every HTTP request must carry `Authorization: Bearer <token>`. Any other
    local user or process can reach a loopback port; the token keeps them out."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            given = headers.get(b"authorization", b"")
            if not secrets.compare_digest(given, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body",
                            "body": b"unauthorized: send the header  Authorization: Bearer <token>\n"})
                return
        await asgi(scope, receive, send)

    return guarded


def serve_http(registry: ModuleRegistry, host: str, port: int, allow_system: bool = False,
               watch: bool = True, token: str | None = None, expose: Iterable[str] | None = None,
               role: str | None = None) -> None:
    """Serve over streamable HTTP at http://HOST:PORT/mcp (loopback addresses only). With a
    `token`, every request must send `Authorization: Bearer <token>`."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    app = ShellcraftMCP(registry, allow_system, expose, role=role)
    _log_startup(app, watch)
    shown = f"[{host}]" if ":" in host else host
    log.info("streamable HTTP on http://%s:%d/mcp", shown, port)
    if token:
        log.info("bearer token: %s  (clients send the header: Authorization: Bearer %s)", token, token)
    else:
        log.warning("no bearer token: any local process can use this server (--no-token)")
    # DNS-rebinding protection: browsers may only reach us under a loopback Host / Origin.
    hosts = sorted({f"{shown}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"})
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                         allowed_origins=[f"http://{h}" for h in hosts])
    asgi = app.server.streamable_http_app(host=host, transport_security=security)
    if token:
        asgi = require_bearer(asgi, token)
    config = uvicorn.Config(asgi, host=host, port=port, log_level="warning", lifespan="on")

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            if watch:
                tg.start_soon(app.watch)
            tg.start_soon(app.watch_reviews)  # the review spool, whatever hot_reload says
            await uvicorn.Server(config).serve()
            tg.cancel_scope.cancel()

    anyio.run(main)
