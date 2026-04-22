"""FastMCP server exposing project-navigation tools to the ralphify agent."""
from __future__ import annotations

import ast
import threading
from pathlib import Path
from typing import Iterable

from fastmcp import FastMCP

from .tasks import add_task, complete_task, load_tasks

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
        "Use get_project_structure or list_files first to orient yourself, then "
        "find_symbol, list_symbols, search_code, or find_references to locate "
        "relevant code before reading full files."
    ),
)

_SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".mypy_cache", "dist", "build"}
_TEXT_EXTENSIONS = {
    ".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg",
    ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".sql", ".sh", ".ps1",
    ".bat", ".cs", ".csproj", ".sln", ".xml",
}
_SUMMARY_LINE_LIMIT = 12


def _require_project() -> Path:
    path = _state["project_path"]
    if not path:
        raise RuntimeError("No project path configured. Select a project in Herdify first.")
    return Path(path).resolve()


def _is_visible(path: Path) -> bool:
    return not any(part in _SKIP_DIRS or part.startswith(".") for part in path.parts)


def _iter_files(root: Path, file_pattern: str = "**/*") -> Iterable[Path]:
    for file_path in root.glob(file_pattern):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(root)
        if not _is_visible(rel):
            continue
        yield file_path


def _read_text_file(file_path: Path) -> str | None:
    if file_path.suffix.lower() not in _TEXT_EXTENSIONS and file_path.name not in {"README", "Dockerfile"}:
        return None
    try:
        return file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, OSError):
        return None


def _python_symbols(file_path: Path) -> list[dict]:
    source = _read_text_file(file_path)
    if source is None:
        return []
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    symbols: list[dict] = []
    source_lines = source.splitlines()

    class SymbolVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _append(self, node: ast.AST, kind: str, name: str) -> None:
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            signature = source_lines[start - 1].strip() if start - 1 < len(source_lines) else name
            qualified = ".".join([*self.stack, name]) if self.stack else name
            symbols.append(
                {
                    "name": name,
                    "qualified_name": qualified,
                    "kind": kind,
                    "line": start,
                    "end_line": end,
                    "signature": signature,
                }
            )

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._append(node, "class", node.name)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            kind = "method" if self.stack else "function"
            self._append(node, kind, node.name)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            kind = "method" if self.stack else "async function"
            self._append(node, kind, node.name)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

    SymbolVisitor().visit(tree)
    return symbols


def _summarize_text(content: str, max_lines: int = _SUMMARY_LINE_LIMIT) -> list[str]:
    summary_lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        summary_lines.append(line)
        if len(summary_lines) >= max_lines:
            break
    return summary_lines


# --- Task tools ---

@mcp.tool()
def get_todos() -> list[dict]:
    """Return all tasks with their title, description, and done status."""
    root = _require_project()
    tasks = load_tasks(str(root))
    return [{"title": t.title, "description": t.description, "done": t.done} for t in tasks]


@mcp.tool()
def add_todo(title: str, description: str = "", files: list[str] | None = None) -> dict:
    """Create a new pending task in TODO.md.

    Args:
        title: Title of the task to create.
        description: Optional longer description for the task.
        files: Optional list of relevant project-relative file paths.
    """
    root = _require_project()
    clean_title = title.strip()
    if not clean_title:
        return {"success": False, "message": "Task title cannot be empty."}

    task = add_task(
        str(root),
        title=clean_title,
        description=description.strip(),
        files=files,
    )
    return {
        "success": True,
        "message": f"Task '{task.title}' created.",
        "task": {
            "title": task.title,
            "description": task.description,
            "done": task.done,
            "files": task.files,
        },
    }


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
def list_files(file_pattern: str = "**/*", max_results: int = 200) -> str:
    """List visible project files matching a glob pattern.

    Args:
        file_pattern: Glob pattern for files to include (default '**/*').
        max_results: Maximum number of files to return (default 200).
    """
    root = _require_project()
    results: list[str] = []

    for file_path in _iter_files(root, file_pattern):
        results.append(str(file_path.relative_to(root)))
        if len(results) >= max_results:
            results.append(f"... (truncated at {max_results} files)")
            break

    if not results:
        return f"No files found for pattern '{file_pattern}'."
    return "\n".join(results)


@mcp.tool()
def list_symbols(file_path: str) -> list[dict]:
    """List Python symbols found in a specific file.

    Args:
        file_path: Project-relative path to a Python file.
    """
    root = _require_project()
    target = (root / file_path).resolve()
    if root not in target.parents and target != root:
        raise RuntimeError("File path must stay within the selected project.")
    if not target.is_file():
        return []
    if target.suffix.lower() != ".py":
        return []

    return _python_symbols(target)


@mcp.tool()
def find_symbol(symbol_name: str) -> list[dict]:
    """Find Python symbol definitions by exact name.

    Args:
        symbol_name: Exact function, method, or class name to look up.
    """
    root = _require_project()
    matches: list[dict] = []

    for py_file in _iter_files(root, "**/*.py"):
        rel = py_file.relative_to(root)
        for symbol in _python_symbols(py_file):
            if symbol["name"] != symbol_name:
                continue
            matches.append(
                {
                    "file": str(rel),
                    "line": symbol["line"],
                    "kind": symbol["kind"],
                    "qualified_name": symbol["qualified_name"],
                    "signature": symbol["signature"],
                }
            )

    return matches


@mcp.tool()
def get_symbol(symbol_name: str) -> str:
    """Return the source code of a Python function or class by name.

    Searches all .py files in the project and returns every matching definition.

    Args:
        symbol_name: Exact name of the function or class to look up.
    """
    root = _require_project()
    results: list[str] = []

    for py_file in _iter_files(root, "**/*.py"):
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

    for file_path in _iter_files(root, file_pattern):
        content = _read_text_file(file_path)
        if content is None:
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


@mcp.tool()
def find_references(symbol_name: str, file_pattern: str = "**/*", max_results: int = 100) -> str:
    """Search for likely references to a symbol name across text files.

    Args:
        symbol_name: Symbol or identifier to search for.
        file_pattern: Glob pattern for files to search (default '**/*').
        max_results: Maximum number of matches to return (default 100).
    """
    root = _require_project()
    results: list[str] = []

    for file_path in _iter_files(root, file_pattern):
        content = _read_text_file(file_path)
        if content is None:
            continue

        for lineno, line in enumerate(content.splitlines(), start=1):
            if symbol_name not in line:
                continue
            rel = file_path.relative_to(root)
            results.append(f"{rel}:{lineno}: {line.rstrip()}")
            if len(results) >= max_results:
                results.append(f"... (truncated at {max_results} results)")
                return "\n".join(results)

    if not results:
        return f"No references found for '{symbol_name}'."
    return "\n".join(results)


@mcp.tool()
def get_file_summary(file_path: str) -> dict:
    """Return a compact summary of a file without returning the entire contents.

    Args:
        file_path: Project-relative path to the file to summarize.
    """
    root = _require_project()
    target = (root / file_path).resolve()
    if root not in target.parents and target != root:
        raise RuntimeError("File path must stay within the selected project.")
    if not target.is_file():
        return {"success": False, "message": f"File '{file_path}' was not found."}

    rel = target.relative_to(root)
    content = _read_text_file(target)
    summary: dict[str, object] = {
        "success": True,
        "file": str(rel),
        "extension": target.suffix.lower(),
        "size_bytes": target.stat().st_size,
    }

    if content is None:
        summary["message"] = "File is not readable as UTF-8 text."
        return summary

    lines = content.splitlines()
    summary["line_count"] = len(lines)
    summary["preview"] = _summarize_text(content)
    if target.suffix.lower() == ".py":
        summary["symbols"] = _python_symbols(target)

    return summary


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
