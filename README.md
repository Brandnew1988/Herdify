# Herdify

> Named after Walter the Corgi - because corgis herd, and Herdify herds your AI agents.

## What is Herdify?

Herdify is a desktop tool that makes it easy to set up ralphify, manage tasks, and give your AI agent better context without manually editing configuration files.

It is built on top of [ralphify](https://github.com/computerlovetech/ralphify) and extends it with a user interface, task management, and a built-in MCP server that helps the agent navigate the project more efficiently.

---

## Stack

- **Python** - all backend logic
- **Flet** - cross-platform desktop UI (Windows, Mac, Linux)
- **FastMCP** - MCP server running inside Herdify
- **ralphify** - the agent loop engine
- **uv** - package management

---

## Features in v1

### 1. Easy setup
- Choose the project folder
- Generate `RALPH.md` automatically
- Start adding Task

No manual editing of YAML frontmatter required.

### 2. Task management
Add, view, and manage tasks directly from the UI:
- Create tasks with a title and description
- See which tasks are done or pending
- Store tasks in `TODO.md` inside the project and keep them synced with the UI

The agent marks tasks as done through the MCP server instead of manually parsing markdown.

### 3. Built-in MCP server
Herdify starts a local MCP server when a ralph session is running. The agent can call:

| Tool | Description |
|------|-------------|
| `add_todo` | Creates a new pending task |
| `get_todos` | Returns all tasks with status |
| `complete_todo` | Marks a task as done |
| `list_files` | Lists visible project files by glob pattern |
| `list_symbols` | Lists Python classes and functions in a file |
| `find_symbol` | Locates Python symbol definitions by exact name |
| `find_references` | Searches likely symbol references across text files |
| `get_file_summary` | Returns a compact file summary with Python symbols |
| `get_project_structure` | Returns the project tree without file contents |
| `get_symbol` | Fetches a function or class by name |
| `search_code` | Simple text search across the codebase |

This lets the agent spend fewer tokens navigating the project and only read full file contents when needed. The newer indexing-style tools are meant to make that cheaper still by letting the agent list files, inspect symbol inventories, and fetch compact summaries before reading code in full.

At the moment, semantic symbol support is Python-only. Tools such as `get_symbol`, `list_symbols`, and `find_symbol` currently understand Python source files, while the more general navigation tools like `list_files`, `search_code`, `find_references`, and `get_file_summary` can still be useful across other text-based projects.

### 4. Ralph control
Start and stop ralph loops directly from the UI with no terminal required.

---

## Getting started

```bash
# Install with uv
uv add herdify

# Open the app
uv run herdify
```

---

## Roadmap (after v1)

- **Memory** - persistent memory across iterations
- **Iteration log** - inspect what the agent did in each iteration
- **Token tracking** - monitor usage over time
- **Multi-project** - manage multiple ralph from the same UI

---

## Project structure

```text
herdify/
|-- src/
|   `-- herdify/
|       |-- main.py          # Flet app entry point
|       |-- ui/              # Flet views and components
|       |   |-- setup_view.py
|       |   |-- tasks_view.py
|       |   `-- control_view.py
|       |-- mcp_server.py    # FastMCP server
|       |-- ralph.py         # ralphify wrapper
|       `-- tasks.py         # TODO.md read/write logic
|-- pyproject.toml
`-- README.md
```
