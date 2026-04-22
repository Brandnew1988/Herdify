"""ralphify subprocess wrapper and RALPH.md generation."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# RALPH.md generation
# ---------------------------------------------------------------------------

# ralphify RALPH.md format:
#   agent  — the CLI command ralphify pipes the prompt into
#   commands — scripts whose output is injected via {{ commands.<name> }}
#
# See: https://github.com/computerlovetech/ralphify

_RALPH_MD_TEMPLATE = """\
---
agent: claude -p --dangerously-skip-permissions --output-format stream-json
commands:
  - name: todos
    run: python -c "from pathlib import Path; print(Path('TODO.md').read_text(encoding='utf-8'))"
  - name: structure
    run: python -c "from pathlib import Path; files=[str(p.relative_to('.')) for p in sorted(Path('.').rglob('*')) if p.is_file() and not any(x in p.parts for x in ('__pycache__','.git','.venv','venv','node_modules'))]; print('\\n'.join(files[:60]))"
---

Du er en autonom kodnings-agent der arbejder i en loop.

## Dine opgaver

{{ commands.todos }}

## Projektstruktur

{{ commands.structure }}

## Tilgængelige MCP-tools (herdify)

Herdify stiller følgende MCP-tools til rådighed via den registrerede `herdify` MCP-server:

- `complete_todo(title)` — Markér en opgave som fuldført (opdaterer TODO.md via Herdify)
- `get_todos()` — Hent aktuelle opgaver med status
- `get_project_structure(max_depth)` — Hent projektets mappestruktur
- `get_symbol(symbol_name)` — Find en funktion eller klasse i kodebasen
- `search_code(query, file_pattern)` — Søg efter tekst i kodebasen

## Regler

1. Kig på dine opgaver ovenfor (markeret med `- [ ]`)
2. Vælg ÉN opgave og løs den
3. Brug MCP-tool `complete_todo` til at markere opgaven som fuldført — redigér IKKE TODO.md direkte
4. Stop efter du har løst opgaven

Opfind ikke nye opgaver. Arbejd kun med hvad der er listet.
"""


def ensure_ralphify(on_output: Callable[[str], None] | None = None) -> bool:
    """Check if ralphify is installed; install it via uv if missing.

    Returns True if ralph is available after the call.
    """
    cb = on_output or (lambda s: None)

    if shutil.which("ralph"):
        return True

    uv = shutil.which("uv")
    if not uv:
        cb(
            "[herdify] ADVARSEL: 'ralph' ikke fundet og 'uv' er ikke installeret.\n"
            "          Installer ralphify manuelt: pip install ralphify\n"
        )
        return False

    cb("[herdify] Installerer ralphify via uv tool install ralphify…\n")
    try:
        result = subprocess.run(
            [uv, "tool", "install", "ralphify"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            cb("[herdify] ralphify installeret og klar.\n")
            return True
        cb(
            f"[herdify] Fejl ved installation af ralphify:\n"
            f"{result.stderr.strip()}\n"
        )
        return False
    except Exception as exc:
        cb(f"[herdify] Fejl ved installation af ralphify: {exc}\n")
        return False


_RALPHIFY_RELEASES_URL = (
    "https://api.github.com/repos/computerlovetech/ralphify/releases/latest"
)


def check_and_upgrade_ralphify(on_output: Callable[[str], None] | None = None) -> None:
    """Fetch latest ralphify release from GitHub and upgrade via uv if behind.

    Silently returns on network errors so startup is never blocked.
    """
    cb = on_output or (lambda s: None)

    # Resolve installed version
    ralph = shutil.which("ralph")
    installed = ""
    if ralph:
        try:
            res = subprocess.run(
                [ralph, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            raw = (res.stdout.strip() or res.stderr.strip())
            installed = raw.split()[-1].lstrip("v") if raw else ""
        except Exception:
            pass

    # Fetch latest release tag from GitHub
    try:
        req = urllib.request.Request(
            _RALPHIFY_RELEASES_URL,
            headers={"User-Agent": "herdify/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        latest = data.get("tag_name", "").lstrip("v")
    except Exception:
        return

    if not latest:
        return

    if installed and installed == latest:
        cb(f"[herdify] ralphify er opdateret (v{latest}).\n")
        return

    # Upgrade
    uv = shutil.which("uv")
    if not uv:
        cb(
            f"[herdify] Ny version af ralphify tilgængelig (v{latest})"
            f"{' (installeret: v' + installed + ')' if installed else ''},"
            " men 'uv' er ikke installeret.\n"
        )
        return

    version_info = f"v{installed} → v{latest}" if installed else f"v{latest}"
    cb(f"[herdify] Opdaterer ralphify {version_info}…\n")
    try:
        result = subprocess.run(
            [uv, "tool", "upgrade", "ralphify"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            cb(f"[herdify] ralphify opdateret til v{latest}.\n")
        else:
            cb(f"[herdify] Fejl ved opdatering: {result.stderr.strip()}\n")
    except Exception as exc:
        cb(f"[herdify] Fejl ved opdatering af ralphify: {exc}\n")


def generate_ralph_md(project_path: str) -> Path:
    """Write a correctly-formatted RALPH.md to *project_path*."""
    ralph_path = Path(project_path) / "RALPH.md"
    ralph_path.write_text(_RALPH_MD_TEMPLATE, encoding="utf-8")
    return ralph_path


# ---------------------------------------------------------------------------
# Process runner
# ---------------------------------------------------------------------------


class RalphRunner:
    """Manages a ralphify subprocess lifecycle."""

    def __init__(
        self,
        project_path: str,
        on_output: Callable[[str], None] | None = None,
        on_stopped: Callable[[], None] | None = None,
    ) -> None:
        self.project_path = project_path
        self.on_output = on_output or (lambda line: None)
        self.on_stopped = on_stopped or (lambda: None)
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        """Start ralph run <project_path>. Returns False if already running."""
        with self._lock:
            if self._running:
                return False

            ralph_cmd = self._find_ralph_cmd()
            if ralph_cmd is None:
                self.on_output(
                    "[herdify] ERROR: Kan ikke finde 'ralph'. "
                    "Installer ralphify: uv tool install ralphify\n"
                )
                return False

            # ralph run <path>  — run indefinitely until stopped
            cmd = ralph_cmd + ["run", self.project_path]
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except FileNotFoundError:
                self.on_output(
                    "[herdify] ERROR: Kan ikke starte ralph. "
                    "Installer ralphify: uv tool install ralphify\n"
                )
                return False

            self._running = True
            self._thread = threading.Thread(target=self._stream_output, daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        """Terminate the ralph subprocess."""
        with self._lock:
            if self._process and self._running:
                self._process.terminate()

    def _find_ralph_cmd(self) -> list[str] | None:
        """Return the base command for ralphify, or None if not found."""
        if shutil.which("ralph"):
            return ["ralph"]
        # Fallback: try via uv tool or python -m
        if shutil.which("uv"):
            return ["uv", "tool", "run", "ralph"]
        return None

    def _stream_output(self) -> None:
        """Background thread: stream stdout lines to the callback."""
        try:
            if self._process is None:
                raise RuntimeError("_stream_output called before process was started")
            if self._process.stdout is None:
                raise RuntimeError("Process stdout is not a pipe")

            for line in self._process.stdout:
                self.on_output(line)
        finally:
            if self._process is not None:
                self._process.wait()
            with self._lock:
                self._running = False
            self.on_stopped()
