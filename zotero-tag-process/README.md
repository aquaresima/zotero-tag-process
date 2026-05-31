# zotero-tag-process

Unified entry point for the Zotero tag enrichment pipeline. Wraps two existing
packages — [`tag-generation`](../tag-generation) and
[`zotero-restructuring`](../zotero-restructuring) — behind a single command and
a single web interface with two workflows.

```
zotero.sqlite (read-only)
        |
        +-- zotero-tag-process serve   <- one web UI, two workflows
                |
                +-- Workflow A: Tag Generation   (/generate/)
                |     Page 1 Setup & Generate, Page 2 Review & Approve
                |     Output: zotero_generatedtags_DATE.sqlite
                |
                +-- Workflow B: Zotero Update     (/update/)
                      Page 1 Import & Apply, Page 2 Stats & Network
```

## Installation

```bash
cd ~/.claude/lib/zotero-tag-process
uv sync
```

This installs the `zotero-tag-process` CLI command and pulls in the two
sub-packages as editable path dependencies, so changes to either tool are
picked up automatically.

## Requirements

- **Ollama** running locally (`ollama serve`, default `http://localhost:11434`).
  `serve` starts it automatically if not already running.
- **Zotero** open with the local API enabled (Preferences -> Advanced ->
  "Allow other applications to communicate"), required only at apply time.
- A readable `zotero.sqlite` (default path from each sub-package's config; set
  `ZOTERO_SQLITE_PATH` to override).

## Workflow A — Generate tags with Ollama

1. **Ingest** your library into a working database:

   ```bash
   zotero-tag-process ingest
   # output: ~/data/zotero_generatedtags_YYYY-MM-DD.sqlite
   ```

2. **Serve** the web UI and open the *Tag Generation* workflow:

   ```bash
   zotero-tag-process serve
   # then visit http://localhost:8000/generate/
   ```

   Click **Generate Tags** to start Ollama. Proposals appear in real time on
   the review page as each batch completes.

3. **Review** proposed tags per paper. Use the confidence slider to bulk-approve;
   approved papers leave the queue.

   You can also run the tagger from the CLI:

   ```bash
   zotero-tag-process generate --model llama3.1:latest --batch-size 2
   ```

## Workflow B — Apply approved tags to Zotero

4. In the web UI, open the *Zotero Update* workflow
   (http://localhost:8000/update/). Select the generatedtags DB, click
   **Import & Normalize**, then **Backup**, then **Apply to Zotero**. Page 2
   shows before/after stats and the tag co-occurrence network.

   Or from the CLI:

   ```bash
   zotero-tag-process import-tags \
     --generated-db ~/data/zotero_generatedtags_DATE.sqlite
   ```

## CLI reference

```
zotero-tag-process serve [--port N]            launch the combined web UI
zotero-tag-process ingest [--sqlite P] [--output P]
zotero-tag-process generate [--db P] [--model TEXT] [--batch-size N]
zotero-tag-process import-tags --generated-db P [--sqlite P]
zotero-tag-process help                        narrative guide
```

Every command also accepts `--help`.

## Configuration

- **`.env`** files live in each sub-package
  (`~/.claude/lib/tag-generation/.env`,
  `~/.claude/lib/zotero-restructuring/.env`). They set `ZOTERO_SQLITE_PATH`,
  Ollama model/URL, worker batch size, confidence threshold, etc.
- **`taxonomy.yaml`** at `~/.claude/lib/zotero-restructuring/taxonomy.yaml`
  controls the system prompt, method vocabulary, and field taxonomy.
- Both tools can still be run standalone:

  ```bash
  cd ~/.claude/lib/tag-generation     && uv run tag-generation serve
  cd ~/.claude/lib/zotero-restructuring && uv run zotero-restructuring serve
  ```

## Troubleshooting

- **Ollama not responding** — confirm `ollama serve` is up and the model is
  pulled (`ollama pull llama3.1:latest`). Check `OLLAMA_BASE_URL`.
- **Zotero API not reachable at apply time** — Zotero must be running with the
  local API enabled; the default endpoint is `http://localhost:23119`.
- **Web links 404 under a prefix** — the combined app rewrites the sub-apps'
  root-relative URLs to carry `/generate` or `/update`. If you customized a
  sub-package template to use protocol-relative or absolute external URLs, they
  are intentionally left untouched.
- **Regenerating tags is safe** — re-running ingest + generate skips papers
  that already have approved/rejected proposals.
```
