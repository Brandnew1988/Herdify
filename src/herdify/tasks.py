"""TODO.md reading and writing for Herdify task management."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    title: str
    description: str = ""
    done: bool = False
    files: list[str] = field(default_factory=list)

    def to_markdown_line(self) -> str:
        status = "x" if self.done else " "
        text = f"{self.title}: {self.description}" if self.description else self.title
        if self.files:
            text = f"{text} [filer: {', '.join(self.files)}]"
        return f"- [{status}] {text}"


_TASK_RE = re.compile(r"^- \[([ x])\] (.+)$")
_FILES_RE = re.compile(r"\s*\[filer: ([^\]]+)\]$")


def load_tasks(project_path: str) -> list[Task]:
    """Parse TODO.md and return all tasks."""
    todo_path = Path(project_path) / "TODO.md"
    if not todo_path.exists():
        return []

    tasks: list[Task] = []
    for raw_line in todo_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        m = _TASK_RE.match(line)
        if not m:
            continue
        done = m.group(1) == "x"
        rest = m.group(2)

        files: list[str] = []
        fm = _FILES_RE.search(rest)
        if fm:
            files = [f.strip() for f in fm.group(1).split(",") if f.strip()]
            rest = rest[:fm.start()]

        if ": " in rest:
            title, description = rest.split(": ", 1)
        else:
            title, description = rest, ""
        tasks.append(Task(title=title.strip(), description=description.strip(), done=done, files=files))

    return tasks


def save_tasks(project_path: str, tasks: list[Task]) -> None:
    """Write tasks back to TODO.md, preserving header."""
    todo_path = Path(project_path) / "TODO.md"
    lines = ["# TODO", ""]
    for task in tasks:
        lines.append(task.to_markdown_line())
    lines.append("")
    todo_path.write_text("\n".join(lines), encoding="utf-8")


def add_task(project_path: str, title: str, description: str = "", files: list[str] | None = None) -> Task:
    """Append a new pending task to TODO.md."""
    tasks = load_tasks(project_path)
    task = Task(title=title, description=description, files=files or [])
    tasks.append(task)
    save_tasks(project_path, tasks)
    return task


def complete_task(project_path: str, title: str) -> bool:
    """Mark the first task matching *title* as done. Returns True if found."""
    tasks = load_tasks(project_path)
    for task in tasks:
        if task.title.lower() == title.lower():
            task.done = True
            save_tasks(project_path, tasks)
            return True
    return False


def delete_task(project_path: str, title: str) -> bool:
    """Remove a task by title. Returns True if removed."""
    tasks = load_tasks(project_path)
    original_len = len(tasks)
    tasks = [t for t in tasks if t.title.lower() != title.lower()]
    if len(tasks) < original_len:
        save_tasks(project_path, tasks)
        return True
    return False


def reopen_task(project_path: str, title: str) -> bool:
    """Set a done task back to pending. Returns True if found."""
    tasks = load_tasks(project_path)
    for task in tasks:
        if task.title.lower() == title.lower():
            task.done = False
            save_tasks(project_path, tasks)
            return True
    return False


def update_task(project_path: str, old_title: str, new_title: str, new_description: str = "", new_files: list[str] | None = None) -> bool:
    """Update title, description, and files of an existing task. Returns True if found."""
    tasks = load_tasks(project_path)
    for task in tasks:
        if task.title.lower() == old_title.lower():
            task.title = new_title.strip()
            task.description = new_description.strip()
            if new_files is not None:
                task.files = new_files
            save_tasks(project_path, tasks)
            return True
    return False


def ensure_todo_exists(project_path: str) -> None:
    """Create an empty TODO.md if it doesn't exist yet."""
    todo_path = Path(project_path) / "TODO.md"
    if not todo_path.exists():
        todo_path.write_text("# TODO\n\n", encoding="utf-8")
