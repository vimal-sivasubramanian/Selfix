"""
Claude Agent SDK worker.

Implements a standard tool-use agentic loop using the Anthropic messages API.
The worker is stateless — all context is passed in the prompt.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool implementations (executed locally when Claude calls them)
# ---------------------------------------------------------------------------

def _tool_read(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"
    try:
        return path.read_text(errors="replace")
    except Exception as e:
        return f"Error reading {file_path}: {e}"


def _tool_glob(pattern: str, path: str | None = None) -> str:
    import glob as _glob
    base = path or "."
    matches = _glob.glob(f"{base}/{pattern}", recursive=True)
    if not matches:
        return "No files matched."
    return "\n".join(sorted(matches))


def _tool_grep(pattern: str, path: str = ".", glob: str | None = None) -> str:
    cmd = ["grep", "-rn", "--include", glob or "*", pattern, path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout or result.stderr
        return output[:8000] if output else "No matches found."
    except Exception as e:
        return f"Error running grep: {e}"


def _tool_edit(file_path: str, old_string: str, new_string: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"
    content = path.read_text(errors="replace")
    if old_string not in content:
        return f"Error: old_string not found in {file_path}"
    new_content = content.replace(old_string, new_string, 1)
    path.write_text(new_content)
    return f"Successfully edited {file_path}"


def _tool_bash(command: str, cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        output = result.stdout + result.stderr
        return output[:8000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 60s"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool schemas passed to the model
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "Read",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or relative path to the file."}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'"},
                "path": {"type": "string", "description": "Base directory to search from (optional)."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents with a regex pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path": {"type": "string", "description": "Directory to search (default: current directory)."},
                "glob": {"type": "string", "description": "File glob filter, e.g. '*.py' (optional)."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Edit",
        "description": "Edit a file by replacing an exact string with a new string.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {"type": "string", "description": "Exact string to replace."},
                "new_string": {"type": "string", "description": "Replacement string."},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Bash",
        "description": "Run a shell command and return its output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "cwd": {"type": "string", "description": "Working directory (optional)."},
            },
            "required": ["command"],
        },
    },
]


def _dispatch_tool(name: str, inputs: dict) -> str:
    if name == "Read":
        return _tool_read(inputs["file_path"])
    if name == "Glob":
        return _tool_glob(inputs["pattern"], inputs.get("path"))
    if name == "Grep":
        return _tool_grep(inputs["pattern"], inputs.get("path", "."), inputs.get("glob"))
    if name == "Edit":
        return _tool_edit(inputs["file_path"], inputs["old_string"], inputs["new_string"])
    if name == "Bash":
        return _tool_bash(inputs["command"], inputs.get("cwd"))
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    text: str       # final assistant text response
    tool_calls: int # how many tool calls were made


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class AgentWorker:
    """
    Stateless Claude agent worker.
    Each call runs a fresh agentic loop with the provided prompt and allowed tools.
    """

    def __init__(
        self,
        model: str = "claude-opus-4-6",
        max_tokens: int = 8192,
        allowed_tools: list[str] | None = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.allowed_tools = set(allowed_tools or ["Read", "Glob", "Grep", "Edit", "Bash"])
        self._client = anthropic.Anthropic()

    def run(self, prompt: str) -> AgentResult:
        """Run the agentic loop synchronously."""
        tools = [t for t in TOOL_SCHEMAS if t["name"] in self.allowed_tools]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tool_call_count = 0
        final_text = ""

        while True:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                tools=tools,
                messages=messages,
            )
            logger.debug("Agent response stop_reason=%s", response.stop_reason)

            # Collect text and tool_use blocks from this response
            assistant_content = []
            tool_uses = []
            for block in response.content:
                assistant_content.append(block)
                if block.type == "text":
                    final_text = block.text
                elif block.type == "tool_use":
                    tool_uses.append(block)

            messages.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == "end_turn" or not tool_uses:
                break

            # Execute tools and add results
            tool_results = []
            for tool_use in tool_uses:
                tool_call_count += 1
                logger.info("Tool call: %s(%s)", tool_use.name, list(tool_use.input.keys()))
                result = _dispatch_tool(tool_use.name, tool_use.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

        return AgentResult(text=final_text, tool_calls=tool_call_count)
