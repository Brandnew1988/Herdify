"""FastMCP server exposing project-navigation tools to the ralphify agent."""
from __future__ import annotations

import ast
import threading
from pathlib import Path

from fastmcp import FastMCP

from .tasks import load_tasks, complete_task

# ---------------------------------------------------------------------------
# Shared mutable state — updated when the user selects a project
# ---------------------------------------------------------------------------

_state: dict[str, str | None] = {"project_path": None}


def set_project_path(path: str) -> None:
    """Update the project path used by all MCP tools."""
    _state["project_path"] = path


def get_project_path() -> str | None:
    return _state["project_path"]


# ---------------------------------------------------------------------------
# FastMCP server & tools
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="herdify",
    instructions=(
        "Tools for navigating a software project and managing its TODO list. "
        "Use get_project_structure first to orient yourself, then get_symbol or "
        "search_code to locate relevant code before reading full files."
    ),
)

_SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".mypy_cache", "dist", "build"}


def _require_project() -> Path:
    path = _state["project_path"]
    if not path:
        raise RuntimeError("No project path configured. Select a project in Herdify first.")
    return Path(path).resolve()


# --- Task tools ---

@mcp.tool()
def get_todos() -> list[dict]:
    """Return all tasks with their title, description, and done status."""
    root = _require_project()
    tasks = load_tasks(str(root))
    return [{"title": t.title, "description": t.description, "done": t.done} for t in tasks]


@mcp.tool()
def complete_todo(title: str) -> dict:
    """Mark the task with the given title as done.

    Args:
        title: Exact title of the task to mark as done (case-insensitive).
    """
    root = _require_project()
    success = complete_task(str(root), title)
    if success:
        return {"success": True, "message": f"Task '{title}' marked as done."}
    return {"success": False, "message": f"Task '{title}' not found in TODO.md."}


# --- Project structure tools ---

@mcp.tool()
def get_project_structure(max_depth: int = 4) -> str:
    """Return an ASCII directory tree of the project (no file contents).

    Args:
        max_depth: How many directory levels to descend (default 4).
    """
    root = _require_project()

    lines: list[str] = [str(root)]

    def _walk(path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return

        visible = [e for e in entries if e.name not in _SKIP_DIRS and not e.name.startswith(".")]
        for i, entry in enumerate(visible):
            connector = "└── " if i == len(visible) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(visible) - 1 else "│   "
                _walk(entry, prefix + extension, depth + 1)

    _walk(root, "", 1)
    return "\n".join(lines)


@mcp.tool()
def get_symbol(symbol_name: str) -> str:
    """Return the source code of a Python function or class by name.

    Searches all .py files in the project and returns every matching definition.

    Args:
        symbol_name: Exact name of the function or class to look up.
    """
    root = _require_project()
    results: list[str] = []

    for py_file in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in py_file.parts):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        source_lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name != symbol_name:
                continue
            start = node.lineno - 1
            end = getattr(node, "end_lineno", start + 30)
            snippet = "\n".join(source_lines[start:end])
            rel = py_file.relative_to(root)
            results.append(f"# {rel}:{node.lineno}\n{snippet}")

    if not results:
        return f"Symbol '{symbol_name}' not found in any .py file."
    return "\n\n---\n\n".join(results)


@mcp.tool()
def search_code(query: str, file_pattern: str = "**/*.py") -> str:
    """Search for a text string across the project codebase.

    Returns up to 100 matching lines with file:line context.

    Args:
        query: Text to search for (case-insensitive).
        file_pattern: Glob pattern for files to search (default '**/*.py').
    """
    root = _require_project()
    results: list[str] = []
    query_lower = query.lower()

    for file_path in root.glob(file_pattern):
        if not file_path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in file_path.parts):
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        for lineno, line in enumerate(content.splitlines(), start=1):
            if query_lower in line.lower():
                rel = file_path.relative_to(root)
                results.append(f"{rel}:{lineno}: {line.rstrip()}")
                if len(results) >= 100:
                    results.append("... (truncated at 100 results)")
                    return "\n".join(results)

    if not results:
        return f"No matches found for '{query}'."
    return "\n".join(results)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_actual_port: int = 0


def get_port() -> int:
    """Return the port the MCP server is running on."""
    return _actual_port


def _find_free_port(host: str = "localhost") -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def start_server(host: str = "localhost") -> threading.Thread:
    """Start the MCP SSE server on a free port in a daemon thread."""
    global _actual_port
    _actual_port = _find_free_port(host)

    def _run() -> None:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        mcp.run(transport="streamable-http", host=host, port=_actual_port, show_banner=False)

    thread = threading.Thread(target=_run, daemon=True, name="herdify-mcp")
    thread.start()
    return thread
