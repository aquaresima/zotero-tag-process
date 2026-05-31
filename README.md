# zotero-tag-process

LLM-assisted tag enrichment pipeline for Zotero libraries.  Generates tags for
all papers with Ollama (local, no API key needed), lets you review and approve
them in a web UI, then applies approved tags back to Zotero via the local API.

---

## Repository layout

```
zotero-tag-process/          ← this repo
├── zotero-tag-process/      ← unified entry-point package  (install this one)
├── tag-generation/          ← Ollama batch tagger + review UI
└── zotero-restructuring/    ← tag normalisation, stats, apply-to-Zotero
```

The three packages are separate Python projects that share the same SQLAlchemy
models.  You only need to install the top-level `zotero-tag-process` package;
it pulls in the other two as local path dependencies.

---

## Requirements

| Dependency | Notes |
|---|---|
| Python ≥ 3.11 | |
| [uv](https://github.com/astral-sh/uv) | fast Python package manager |
| [Ollama](https://ollama.com) | local LLM runtime — runs on CPU or GPU |
| Zotero 7 desktop app | must be open with the local API enabled |
| `llama3.1:latest` (or any chat model) | pulled via `ollama pull llama3.1` |

### Enable the Zotero local API

Open Zotero → Preferences → Advanced → Miscellaneous → **Enable local API**
(creates a server at `http://localhost:23119`).

---

## Installation

```bash
git clone https://github.com/aquaresi/zotero-tag-process.git
cd zotero-tag-process/zotero-tag-process
uv sync
```

That installs the `zotero-tag-process` command into the local venv.  To make
it available system-wide:

```bash
ln -s "$(pwd)/.venv/bin/zotero-tag-process" ~/.local/bin/zotero-tag-process
```

### Pull the Ollama model

```bash
ollama pull llama3.1       # ~4 GB, runs on CPU
# or a smaller/faster alternative:
ollama pull llama3.2
```

---

## Configuration

Copy the example env file and fill in any overrides (all fields are optional —
defaults work for a standard Zotero installation):

```bash
cp zotero-restructuring/.env.example zotero-restructuring/.env
```

| Variable | Default | Description |
|---|---|---|
| `ZOTERO_SQLITE_PATH` | `~/Zotero/zotero.sqlite` | Path to your Zotero database |
| `OLLAMA_MODEL` | `llama3.1:latest` | Model name passed to Ollama |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |
| `SIMULATION_DB_PATH` | `~/data/zotero_restructure.db` | Working database |
| `ZOTERO_API_KEY` | — | Only needed to apply tags via the Zotero cloud API (optional; local API does not require a key) |

The tag vocabulary, method list, and LLM system prompt are in
`zotero-restructuring/taxonomy.yaml` — edit that file to customise what tags
the model proposes.

---

## Workflow

### Step 1 — ingest your library

```bash
zotero-tag-process ingest
```

Reads `zotero.sqlite`, normalises all existing tags, and writes a working
snapshot to `~/data/zotero_generatedtags_YYYY-MM-DD.sqlite`.  Safe to re-run;
papers with already-reviewed proposals are skipped.

### Step 2 — generate tags with Ollama

Start Ollama in a terminal (or as a background service):

```bash
ollama serve
```

Then run the tagger:

```bash
zotero-tag-process generate
```

Each paper gets 10–15 proposed tags across three categories:

- **general** — freeform descriptive tags (e.g. `dendritic computation`)
- **method** — controlled vocabulary of experimental/computational methods
- **field** — two-level taxonomy (e.g. `area/auditory-cortex`, `model/spiking-network`)

Progress is logged to the terminal (`[item N] general (...) methods (...) fields (...)`).

### Step 3 — review in the web UI

```bash
zotero-tag-process serve
# open http://localhost:8000
```

- **Tag Generation** page: approve/reject per tag or bulk-approve above a
  confidence threshold.  Papers move off the queue as you approve them.
- **Zotero Update** page: import approved tags, back up `zotero.sqlite`, apply.

### Step 4 — apply to Zotero

Either use the web UI (Zotero Update → Apply) or the CLI:

```bash
zotero-tag-process import-tags --generated-db ~/data/zotero_generatedtags_DATE.sqlite
```

This writes approved tags to Zotero via the local HTTP API.  The source
`zotero.sqlite` is never written directly; a `.bak.<timestamp>` backup is
created automatically before any write.

---

## CLI reference

```
zotero-tag-process serve             launch unified web UI  (default port 8000)
zotero-tag-process serve --port N    custom port

zotero-tag-process ingest            ingest zotero.sqlite → generatedtags DB
  --sqlite PATH                      override zotero.sqlite location
  --output PATH                      override output DB path

zotero-tag-process generate          run Ollama tagger
  --db PATH                          override generatedtags DB
  --model TEXT                       Ollama model  (default: llama3.1:latest)
  --batch-size N                     papers per LLM call  (default: 2)

zotero-tag-process import-tags       import approved tags + apply to Zotero
  --generated-db PATH  (required)    path to zotero_generatedtags_*.sqlite
  --sqlite PATH                      override zotero.sqlite

zotero-tag-process help              narrative pipeline guide
```

---

## Troubleshooting

**`Ollama not reachable`** — run `ollama serve` in a separate terminal, or
install Ollama as a launchd/systemd service.

**`zotero.sqlite` not found** — set `ZOTERO_SQLITE_PATH` in `.env`, or pass
`--sqlite /path/to/zotero.sqlite` on the command line.

**`Connection refused` on apply** — make sure Zotero is open and the local API
is enabled (Preferences → Advanced → Miscellaneous).

**Tags look wrong after generation** — edit `zotero-restructuring/taxonomy.yaml`
to adjust the system prompt, method vocabulary, or field taxonomy, then re-run
`generate` (papers with only `pending` proposals are regenerated automatically).
