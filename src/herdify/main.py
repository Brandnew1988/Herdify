"""Herdify — single-page desktop app."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import flet as ft

from .mcp_server import get_port, set_project_path, start_server
from .ralph import (RalphRunner, check_and_upgrade_ralphify, ensure_ralphify,
                    generate_ralph_md)
from .tasks import (Task, add_task, complete_task, delete_task,
                    ensure_todo_exists, load_tasks, reopen_task, update_task)

MCP_SERVER_NAME = "herdify"

_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".tox",
}


def _list_project_files(project_path: str) -> list[str]:
    """Return files in *project_path* relative to root, excluding common ignore dirs."""
    root = Path(project_path)
    result: list[str] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in _IGNORE_DIRS or part.startswith(".") for part in rel.parts[:-1]):
                continue
            result.append(str(rel).replace("\\", "/"))
    except Exception:
        pass
    return sorted(result)


def _ensure_claude_mcp() -> tuple[bool, str]:
    """Register Herdify MCP server in Claude's global config, always with current port.

    Removes any existing registration first so the SSE URL stays in sync with
    the dynamically-assigned port chosen at startup.

    Returns (success, message).
    """
    claude = shutil.which("claude")
    if not claude:
        return False, "Claude CLI not found - install Claude Code first."

    mcp_url = f"http://localhost:{get_port()}/mcp"

    # Remove stale registration (port changes on every Herdify start)
    try:
        subprocess.run(
            [claude, "mcp", "remove", MCP_SERVER_NAME, "-s", "user"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass

    # Register with the current port
    try:
        result = subprocess.run(
            [claude, "mcp", "add", MCP_SERVER_NAME,
             "--transport", "http", "--scope", "user", mcp_url],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return True, f"MCP server '{MCP_SERVER_NAME}' registered (port {get_port()})."
        return False, f"Could not add MCP: {result.stderr.strip()}"
    except Exception as exc:
        return False, f"Error: {exc}"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

BG = "#111318"
PANEL = "#1c1f26"
BORDER = "#2a2d35"
ACCENT = "#4f7fff"
TEXT = "#e2e4e9"
TEXT_DIM = "#686b74"
GREEN = "#3ecf6a"
RED = "#f25c5c"
ORANGE = "#f5a623"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class AppState:
    def __init__(self) -> None:
        self.project_path: str = ""
        self.runner: RalphRunner | None = None
        self.mcp_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def _build_app(page: ft.Page) -> None:
    page.title = "Herdify"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1200
    page.window.height = 800
    page.window.min_width = 900
    page.window.min_height = 600

    state = AppState()
    state.mcp_thread = start_server()

    # Register MCP server in Claude's global config (one-time, background)
    mcp_dot = ft.Icon(ft.Icons.CIRCLE, color=ORANGE, size=8, tooltip="MCP: checking…")

    def _register_mcp() -> None:
        ok, msg = _ensure_claude_mcp()
        mcp_dot.color = GREEN if ok else RED
        mcp_dot.tooltip = f"MCP: {msg}"
        page.update()

    threading.Thread(target=_register_mcp, daemon=True).start()

    # Check / install ralphify on startup (background)
    ralph_dot = ft.Icon(ft.Icons.CIRCLE, color=ORANGE, size=8, tooltip="ralphify: checking…")

    def _check_ralphify() -> None:
        def _cb(msg: str) -> None:
            ralph_dot.tooltip = f"ralphify: {msg.strip()}"
            page.update()

        ok = ensure_ralphify(on_output=_cb)
        ralph_dot.color = GREEN if ok else RED
        ralph_dot.tooltip = "ralphify: ready" if ok else "ralphify: not installed"
        page.update()

        if ok:
            check_and_upgrade_ralphify(on_output=_cb)

    threading.Thread(target=_check_ralphify, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Top bar widgets                                                      #
    # ------------------------------------------------------------------ #

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    path_field = ft.TextField(
        hint_text="Choose project folder...",
        expand=True,
        read_only=True,
        bgcolor=PANEL,
        border_color=BORDER,
        focused_border_color=ACCENT,
        color=TEXT,
        hint_style=ft.TextStyle(color=TEXT_DIM),
        height=40,
        content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
    )

    status_dot = ft.Icon(ft.Icons.CIRCLE, color=RED, size=10)
    status_label = ft.Text("Stopped", size=13, color=TEXT_DIM)

    _total_cost: list[float] = [0.0]
    cost_label = ft.Text("$0.0000", size=12, color=TEXT_DIM, tooltip="Accumulated token cost since the last start")

    start_btn = ft.ElevatedButton(
        "Start",
        icon=ft.Icons.PLAY_ARROW_ROUNDED,
        style=ft.ButtonStyle(
            bgcolor=GREEN,
            color="#111318",
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=18, vertical=10),
        ),
    )
    stop_btn = ft.ElevatedButton(
        "Stop",
        icon=ft.Icons.STOP_ROUNDED,
        disabled=True,
        style=ft.ButtonStyle(
            bgcolor=RED,
            color="#111318",
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=18, vertical=10),
        ),
    )

    # ------------------------------------------------------------------ #
    # Tasks panel                                                          #
    # ------------------------------------------------------------------ #

    task_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)

    # ------------------------------------------------------------------ #
    # Log panel                                                            #
    # ------------------------------------------------------------------ #

    log_field = ft.TextField(
        value="",
        multiline=True,
        read_only=True,
        expand=True,
        min_lines=10,
        bgcolor="#0a0c10",
        border_color=BORDER,
        focused_border_color=BORDER,
        color=TEXT,
        text_style=ft.TextStyle(size=12, font_family="monospace"),
        content_padding=ft.padding.all(10),
        border_radius=8,
    )

    def _log(text: str) -> None:
        new_text = text.rstrip("\n")
        if log_field.value:
            log_field.value += "\n" + new_text
        else:
            log_field.value = new_text
        # Try to extract cost from stream-json output (claude --output-format stream-json)
        try:
            data = json.loads(new_text)
            cost = data.get("cost_usd")
            if cost is not None:
                _total_cost[0] += float(cost)
                cost_label.value = f"${_total_cost[0]:.4f}"
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            pass
        page.update()

    # ------------------------------------------------------------------ #
    # Task dialog (create + edit)                                          #
    # ------------------------------------------------------------------ #

    _dialog_title_field = ft.TextField(
        label="Title",
        autofocus=True,
        bgcolor=PANEL,
        border_color=BORDER,
        focused_border_color=ACCENT,
        color=TEXT,
        label_style=ft.TextStyle(color=TEXT_DIM),
        border_radius=8,
    )
    _dialog_desc_field = ft.TextField(
        label="Description (optional)",
        multiline=True,
        min_lines=3,
        max_lines=6,
        bgcolor=PANEL,
        border_color=BORDER,
        focused_border_color=ACCENT,
        color=TEXT,
        label_style=ft.TextStyle(color=TEXT_DIM),
        border_radius=8,
    )
    _dialog_error = ft.Text("", color=RED, size=12)
    _editing_task: list[Task | None] = [None]  # mutable container for closure
    _all_project_files: list[list[str]] = [[]]
    _selected_files: list[list[str]] = [[]]

    _dialog_files_chips = ft.Row(wrap=True, spacing=4, run_spacing=4)

    # --- Multi-select dropdown for file suggestions ---
    _files_dropdown_open: list[bool] = [False]
    _files_dropdown_label = ft.Text("Choose relevant files...", size=12, color=TEXT_DIM, expand=True)
    _files_dropdown_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, color=TEXT_DIM, size=20)

    _files_inner_search = ft.TextField(
        hint_text="Filter files...",
        bgcolor=PANEL,
        border_color=BORDER,
        focused_border_color=ACCENT,
        color=TEXT,
        hint_style=ft.TextStyle(color=TEXT_DIM),
        border_radius=6,
        height=36,
        content_padding=ft.padding.symmetric(horizontal=8, vertical=4),
    )
    _files_checkbox_list = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=0, height=180)

    _files_dropdown_panel = ft.Container(
        content=ft.Column(
            [_files_inner_search, _files_checkbox_list],
            spacing=6,
        ),
        bgcolor="#0a0c10",
        border=ft.border.all(1, BORDER),
        border_radius=8,
        padding=ft.padding.all(8),
        visible=False,
    )

    _files_dropdown_btn = ft.Container(
        content=ft.Row(
            [_files_dropdown_label, _files_dropdown_arrow],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=PANEL,
        border=ft.border.all(1, BORDER),
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=12, vertical=8),
    )

    def _update_dropdown_label() -> None:
        n = len(_selected_files[0])
        if n == 0:
            _files_dropdown_label.value = "Choose relevant files..."
            _files_dropdown_label.color = TEXT_DIM
        else:
            _files_dropdown_label.value = f"{n} file{'s' if n > 1 else ''} selected"
            _files_dropdown_label.color = TEXT

    def _rebuild_file_list(query: str) -> None:
        q = query.lower()
        _files_checkbox_list.controls.clear()
        files_to_show = [
            f for f in _all_project_files[0]
            if not q or q in f.lower()
        ][:60]
        for fname in files_to_show:
            checked = fname in _selected_files[0]

            def _toggle_file(e: ft.ControlEvent, f: str = fname) -> None:
                if e.control.value:
                    if f not in _selected_files[0]:
                        _selected_files[0].append(f)
                else:
                    if f in _selected_files[0]:
                        _selected_files[0].remove(f)
                _update_file_chips()
                _update_dropdown_label()
                page.update()

            _files_checkbox_list.controls.append(
                ft.Container(
                    content=ft.Checkbox(
                        label=fname,
                        value=checked,
                        on_change=_toggle_file,
                        label_style=ft.TextStyle(size=11, color=TEXT),
                        fill_color={ft.ControlState.SELECTED: ACCENT},
                        check_color=ft.Colors.WHITE,
                    ),
                    padding=ft.padding.symmetric(horizontal=4, vertical=1),
                )
            )
        page.update()

    def _filter_files(e: ft.ControlEvent) -> None:
        _rebuild_file_list(e.control.value or "")

    _files_inner_search.on_change = _filter_files

    def _toggle_files_dropdown(_e: ft.ControlEvent) -> None:
        _files_dropdown_open[0] = not _files_dropdown_open[0]
        is_open = _files_dropdown_open[0]
        _files_dropdown_panel.visible = is_open
        _files_dropdown_arrow.name = (
            ft.Icons.ARROW_DROP_UP if is_open else ft.Icons.ARROW_DROP_DOWN
        )
        if is_open:
            _rebuild_file_list(_files_inner_search.value or "")
        else:
            page.update()

    _files_dropdown_gesture = ft.GestureDetector(
        content=_files_dropdown_btn,
        on_tap=_toggle_files_dropdown,
        mouse_cursor=ft.MouseCursor.CLICK,
    )

    def _update_file_chips() -> None:
        _dialog_files_chips.controls.clear()
        for fname in _selected_files[0]:
            def _remove(_e: ft.ControlEvent, f: str = fname) -> None:
                if f in _selected_files[0]:
                    _selected_files[0].remove(f)
                _update_file_chips()
                _update_dropdown_label()
                if _files_dropdown_open[0]:
                    _rebuild_file_list(_files_inner_search.value or "")
                page.update()

            _dialog_files_chips.controls.append(
                ft.Chip(
                    label=ft.Text(fname, size=11, color=TEXT),
                    on_delete=_remove,
                    bgcolor=BORDER,
                    delete_icon_color=TEXT_DIM,
                )
            )

    def _open_task_dialog(task: Task | None = None) -> None:
        if not state.project_path:
            return
        _editing_task[0] = task
        _dialog_title_field.value = task.title if task else ""
        _dialog_desc_field.value = task.description if task else ""
        _selected_files[0] = list(task.files) if task else []
        _files_inner_search.value = ""
        _files_dropdown_open[0] = False
        _files_dropdown_panel.visible = False
        _files_dropdown_arrow.name = ft.Icons.ARROW_DROP_DOWN
        _dialog_error.value = ""
        _update_file_chips()
        _update_dropdown_label()

        def _load_files():
            _all_project_files[0] = _list_project_files(state.project_path)
        threading.Thread(target=_load_files, daemon=True).start()

        page.show_dialog(_task_dialog)

    def _save_task(_: ft.ControlEvent) -> None:
        title = (_dialog_title_field.value or "").strip()
        if not title:
            _dialog_error.value = "Title cannot be empty."
            page.update()
            return
        desc = (_dialog_desc_field.value or "").strip()
        files = list(_selected_files[0])
        existing = _editing_task[0]
        if existing:
            update_task(state.project_path, existing.title, title, desc, files)
        else:
            add_task(state.project_path, title, desc, files)
        page.pop_dialog()
        _reload_tasks()

    _task_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Task", color=TEXT, weight=ft.FontWeight.BOLD),
        bgcolor=PANEL,
        content=ft.Column(
            [
                _dialog_title_field,
                _dialog_desc_field,
                _files_dropdown_gesture,
                _files_dropdown_panel,
                _dialog_files_chips,
                _dialog_error,
            ],
            spacing=16,
            tight=True,
            width=560,
        ),
        actions=[
            ft.TextButton(
                "Cancel",
                style=ft.ButtonStyle(color=TEXT_DIM),
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.ElevatedButton(
                "Save",
                style=ft.ButtonStyle(
                    bgcolor=ACCENT,
                    color=ft.Colors.WHITE,
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
                on_click=_save_task,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    # ------------------------------------------------------------------ #
    # Task helpers                                                         #
    # ------------------------------------------------------------------ #

    def _task_row(task: Task) -> ft.Container:
        is_done = task.done
        done_icon = ft.Icon(
            ft.Icons.CHECK_CIRCLE_ROUNDED if is_done else ft.Icons.RADIO_BUTTON_UNCHECKED_ROUNDED,
            color=GREEN if is_done else TEXT_DIM,
            size=16,
        )
        title_text = ft.Text(
            task.title,
            size=13,
            color=TEXT_DIM if is_done else TEXT,
            expand=True,
        )
        desc_text = ft.Text(
            task.description,
            size=11,
            color=TEXT_DIM,
        ) if task.description else None
        files_label = ft.Text(
            "Files: " + ", ".join(task.files[:3]) + ("..." if len(task.files) > 3 else ""),
            size=10,
            color=TEXT_DIM,
            italic=True,
        ) if task.files else None

        def _toggle_status(_: ft.ControlEvent) -> None:
            if is_done:
                reopen_task(state.project_path, task.title)
            else:
                complete_task(state.project_path, task.title)
            _reload_tasks()

        def _delete(_: ft.ControlEvent) -> None:
            delete_task(state.project_path, task.title)
            _reload_tasks()

        extras = [w for w in [desc_text, files_label] if w is not None]
        title_col = ft.Column(
            [title_text] + extras,
            spacing=2,
            expand=True,
        )

        return ft.Container(
            content=ft.Row(
                [
                    ft.GestureDetector(
                        content=done_icon,
                        on_tap=_toggle_status,
                        mouse_cursor=ft.MouseCursor.CLICK,
                        tooltip="Mark as done / reopen",
                    ),
                    ft.GestureDetector(
                        content=title_col,
                        on_tap=lambda _, t=task: _open_task_dialog(t),
                        mouse_cursor=ft.MouseCursor.CLICK,
                        tooltip="Edit",
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_color=TEXT_DIM,
                        icon_size=14,
                        tooltip="Delete",
                        on_click=_delete,
                        style=ft.ButtonStyle(
                            padding=ft.padding.all(4),
                            overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                        ),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            bgcolor=PANEL,
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            border=ft.border.all(1, BORDER),
        )

    def _reload_tasks() -> None:
        task_list.controls.clear()
        if not state.project_path:
            task_list.controls.append(
                ft.Text("Choose a folder to view tasks.", size=12, color=TEXT_DIM)
            )
            page.update()
            return

        tasks = load_tasks(state.project_path)
        pending = [t for t in tasks if not t.done]
        done = [t for t in tasks if t.done]

        if not tasks:
            task_list.controls.append(
                ft.Text("No tasks yet - press + to add one.", size=12, color=TEXT_DIM)
            )
        else:
            if pending:
                task_list.controls.append(
                    ft.Text(
                        f"TODO ({len(pending)})",
                        size=10,
                        weight=ft.FontWeight.BOLD,
                        color=TEXT_DIM,
                        style=ft.TextStyle(letter_spacing=1.0),
                    )
                )
                for t in pending:
                    task_list.controls.append(_task_row(t))
            if done:
                task_list.controls.append(
                    ft.Text(
                        f"COMPLETED ({len(done)})",
                        size=10,
                        weight=ft.FontWeight.BOLD,
                        color=TEXT_DIM,
                        style=ft.TextStyle(letter_spacing=1.0),
                    )
                )
                for t in done:
                    task_list.controls.append(_task_row(t))

        page.update()

    # ------------------------------------------------------------------ #
    # Agent helpers                                                        #
    # ------------------------------------------------------------------ #

    def _set_running(running: bool) -> None:
        status_dot.color = GREEN if running else RED
        status_label.value = "Running" if running else "Stopped"
        start_btn.disabled = running
        stop_btn.disabled = not running
        page.update()

    def _on_stopped() -> None:
        _set_running(False)
        _log("[herdify] Agent stopped.")

    def _start(_: ft.ControlEvent) -> None:
        if not state.project_path:
            _log("[herdify] Choose a project folder first.")
            return
        _total_cost[0] = 0.0
        cost_label.value = "$0.0000"
        state.runner = RalphRunner(
            project_path=state.project_path,
            on_output=_log,
            on_stopped=_on_stopped,
        )
        if state.runner.start():
            _set_running(True)
            _log("[herdify] Herding ralphify...")
        else:
            _log("[herdify] Could not start - is ralphify installed?")

    def _stop(_: ft.ControlEvent) -> None:
        if state.runner:
            state.runner.stop()
        _set_running(False)
        _log("[herdify] Stop signal sent.")

    start_btn.on_click = _start

    # ------------------------------------------------------------------ #
    # Auto-reload: watch TODO.md for changes                               #
    # ------------------------------------------------------------------ #

    def _watch_todo() -> None:
        last_mtime: float = 0.0
        while True:
            time.sleep(2)
            if not state.project_path:
                continue
            todo = Path(state.project_path) / "TODO.md"
            try:
                mtime = todo.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime != last_mtime:
                last_mtime = mtime
                _reload_tasks()
                threading.Thread(target=_load_git_history, daemon=True).start()
                if state.runner and state.runner.running:
                    tasks = load_tasks(state.project_path)
                    if tasks and all(t.done for t in tasks):
                        _log("[herdify] All tasks are completed - stopping agent automatically.")
                        state.runner.stop()

    threading.Thread(target=_watch_todo, daemon=True).start()

    def _watch_git() -> None:
        last_head: str = ""
        while True:
            time.sleep(15)
            if not state.project_path:
                continue
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                    cwd=state.project_path,
                )
                head = result.stdout.strip()
            except Exception:
                continue
            if head and head != last_head:
                last_head = head
                _load_git_history()

    threading.Thread(target=_watch_git, daemon=True).start()
    stop_btn.on_click = _stop

    # ------------------------------------------------------------------ #
    # Folder picker                                                        #
    # ------------------------------------------------------------------ #

    async def _browse(_: ft.ControlEvent) -> None:
        path = await file_picker.get_directory_path(dialog_title="Choose project folder")
        if not path:
            return
        path_field.value = path
        state.project_path = path
        set_project_path(path)
        generate_ralph_md(path)
        ensure_todo_exists(path)
        _log(f"[herdify] Projekt: {path}")
        _reload_tasks()
        threading.Thread(target=_load_git_history, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Layout                                                               #
    # ------------------------------------------------------------------ #

    top_bar = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.FOLDER_ROUNDED, color=ACCENT, size=18),
                path_field,
                ft.ElevatedButton(
                    "Choose folder",
                    icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                    on_click=_browse,
                    style=ft.ButtonStyle(
                        bgcolor=PANEL,
                        color=TEXT,
                        side=ft.BorderSide(1, BORDER),
                        shape=ft.RoundedRectangleBorder(radius=8),
                        padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    ),
                ),
                ft.Container(width=16),
                mcp_dot,
                ft.Text("MCP", size=11, color=TEXT_DIM),
                ft.Container(width=8),
                ralph_dot,
                ft.Text("ralph", size=11, color=TEXT_DIM),
                ft.Container(width=12),
                status_dot,
                status_label,
                ft.Container(width=8),
                ft.Icon(ft.Icons.MONETIZATION_ON_ROUNDED, color=TEXT_DIM, size=14),
                cost_label,
                ft.Container(width=8),
                start_btn,
                stop_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        bgcolor="#0d0f14",
        padding=ft.padding.symmetric(horizontal=16, vertical=12),
        border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
    )

    tasks_panel = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("TASKS", size=11, weight=ft.FontWeight.BOLD,
                                color=TEXT_DIM, style=ft.TextStyle(letter_spacing=1.2),
                                expand=True),
                        ft.IconButton(
                            icon=ft.Icons.ADD_ROUNDED,
                            icon_color=ACCENT,
                            icon_size=18,
                            tooltip="New task",
                            on_click=lambda _: _open_task_dialog(),
                            style=ft.ButtonStyle(
                                padding=ft.padding.all(4),
                                overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                            ),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                task_list,
            ],
            spacing=10,
            expand=True,
        ),
        width=380,
        padding=ft.padding.all(16),
        border=ft.border.only(right=ft.BorderSide(1, BORDER)),
    )

    def _clear_log(_e):
        log_field.value = ""
        page.update()

    log_panel = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("OUTPUT", size=11, weight=ft.FontWeight.BOLD,
                                color=TEXT_DIM, style=ft.TextStyle(letter_spacing=1.2)),
                        ft.Container(expand=True),
                        ft.TextButton(
                            "Clear",
                            style=ft.ButtonStyle(color=TEXT_DIM),
                            on_click=_clear_log,
                        ),
                    ],
                ),
                log_field,
            ],
            spacing=10,
            expand=True,
        ),
        expand=True,
        padding=ft.padding.all(16),
    )

    # ------------------------------------------------------------------ #
    # Git history sidebar                                                  #
    # ------------------------------------------------------------------ #

    git_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)

    def _load_git_history() -> None:
        if not state.project_path:
            git_list.controls.clear()
            git_list.controls.append(
                ft.Text("Choose a folder to view git history.", size=12, color=TEXT_DIM)
            )
            page.update()
            return

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--format=%H\x1f%h\x1f%s\x1f%an\x1f%ar", "-30"],
                capture_output=True, text=True, timeout=10,
                cwd=state.project_path,
            )
            lines = [l for l in result.stdout.strip().splitlines() if l]
        except Exception:
            lines = []

        git_list.controls.clear()
        if not lines:
            git_list.controls.append(
                ft.Text("No commits found.", size=12, color=TEXT_DIM)
            )
        else:
            for line in lines:
                parts = line.split("\x1f", 4)
                if len(parts) < 5:
                    continue
                full_hash, short_hash, subject, author, rel_time = parts
                git_list.controls.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            short_hash,
                                            size=10,
                                            color=ACCENT,
                                            font_family="monospace",
                                            selectable=True,
                                        ),
                                        ft.Text(rel_time, size=10, color=TEXT_DIM),
                                    ],
                                    spacing=6,
                                ),
                                ft.Text(
                                    subject,
                                    size=12,
                                    color=TEXT,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    max_lines=2,
                                ),
                                ft.Text(author, size=10, color=TEXT_DIM),
                            ],
                            spacing=2,
                        ),
                        bgcolor=PANEL,
                        border_radius=8,
                        padding=ft.padding.symmetric(horizontal=10, vertical=8),
                        border=ft.border.all(1, BORDER),
                        tooltip=full_hash,
                    )
                )
        page.update()

    git_panel = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("GIT", size=11, weight=ft.FontWeight.BOLD,
                                color=TEXT_DIM, style=ft.TextStyle(letter_spacing=1.2),
                                expand=True),
                        ft.IconButton(
                            icon=ft.Icons.REFRESH_ROUNDED,
                            icon_color=TEXT_DIM,
                            icon_size=16,
                            tooltip="Refresh git history",
                            on_click=lambda _: threading.Thread(
                                target=_load_git_history, daemon=True
                            ).start(),
                            style=ft.ButtonStyle(
                                padding=ft.padding.all(4),
                                overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                            ),
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                git_list,
            ],
            spacing=10,
            expand=True,
        ),
        width=300,
        padding=ft.padding.all(16),
        border=ft.border.only(left=ft.BorderSide(1, BORDER)),
    )

    content_row = ft.Row(
        [tasks_panel, log_panel, git_panel],
        expand=True,
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    page.add(
        ft.Column(
            [top_bar, content_row],
            expand=True,
            spacing=0,
        )
    )

    _reload_tasks()


def cli() -> None:
    ft.run(_build_app)


if __name__ == "__main__":
    cli()
