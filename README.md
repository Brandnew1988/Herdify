# Herdify

> Named after Walter the Corgi — because corgis herd, and Herdify herds your AI agents.

## Hvad er Herdify?

Herdify er et desktop tool der gør det nemt at sætte ralphify op, styre tasks og give din AI-agent bedre kontekst — uden at du behøver rode med konfigurationsfiler i hånden.

Det er bygget oven på [ralphify](https://github.com/computerlovetech/ralphify) og udvider det med en brugergrænseflade, task-styring og en indbygget MCP-server som agenten kan bruge til at navigere projektet mere effektivt.

---

## Stack

- **Python** — alt backend-logik
- **Flet** — cross-platform desktop UI (Windows, Mac, Linux)
- **FastMCP** — MCP-server der kører inde i Herdify
- **ralphify** — selve agent-loop motoren
- **uv** — package management

---

## Funktioner i v1

### 1. Nem opsætning
`herdify init` guider dig igennem opsætningen af et nyt ralph-projekt:
- Vælg projekt-mappe
- Definer hvilken agent der skal bruges
- Generer `RALPH.md` automatisk

Ingen manuel redigering af YAML-frontmatter.

### 2. Task styring
Tilføj, se og administrer tasks direkte fra UI'et:
- Opret tasks med titel og beskrivelse
- Se hvilke tasks der er done/pending
- Tasks gemmes i `TODO.md` i projektet og synkroniseres med UI

Agenten markerer tasks som done via MCP-serveren — ikke ved at parse markdown i hånden.

### 3. Indbygget MCP-server
Herdify starter en lokal MCP-server når en ralph-session kører. Agenten kan kalde:

| Tool | Beskrivelse |
|------|-------------|
| `get_todos` | Returnerer alle tasks med status |
| `complete_todo` | Markerer en task som done |
| `get_project_structure` | Returnerer filtræet uden indhold |
| `get_symbol` | Henter én funktion/klasse ved navn |
| `search_code` | Simpel tekst-søgning i kodebasen |

Det betyder agenten bruger få tokens på at *navigere* projektet og kun henter fuldt filindhold når det er nødvendigt.

### 4. Ralph kontrol
Start og stop ralph-loops direkte fra UI'et — ingen terminal nødvendig.

---

## Kom i gang

```bash
# Install med uv
uv add herdify

# Åbn appen
herdify
```

---

## Roadmap (efter v1)

- **Memory via Qdrant** — persistent memory på tværs af iterationer (Docker-baseret)
- **Iteration log** — se hvad agenten lavede i hver iteration
- **Token tracking** — følg forbrug over tid
- **Multi-projekt** — håndter flere ralph-projekter fra samme UI

---

## Projektstruktur

```
herdify/
├── src/
│   └── herdify/
│       ├── main.py          # Flet app entry point
│       ├── ui/              # Flet views og komponenter
│       │   ├── setup_view.py
│       │   ├── tasks_view.py
│       │   └── control_view.py
│       ├── mcp_server.py    # FastMCP server
│       ├── ralph.py         # ralphify wrapper
│       └── tasks.py         # TODO.md læsning/skrivning
├── pyproject.toml
└── README.md
```
