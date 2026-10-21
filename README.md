# nyc-zr — the NYC Zoning Resolution, made searchable for agents

The full text of the [NYC Zoning Resolution](https://zr.planning.nyc.gov) (14 Articles, 101 chapters,
~4,000 sections, the 12-10 definitions, and the appendices) scraped into:

| Layer | What | Why |
| --- | --- | --- |
| `sections/**.md` | one markdown file per section with YAML frontmatter (id, title, article, chapter, parent, last-amended date, defined terms, cross-refs, districts mentioned, source URL) | grep-able, diff-able, git-friendly source of truth |
| `data/*.jsonl`, `data/toc.json` | the same data as JSON records plus the outline | what the index is built from, so a clone is self-contained |
| `data/zr.sqlite` | SQLite with FTS5 (BM25) over sections, definitions, appendices; built locally on first use | fast, deterministic search with snippets |
| `scripts/zr.py` | CLI: search / get / define / chapter / toc / district / history | what an agent runs; plain `python3`, no install |
| `mcp/server.py` | the same functions as an MCP server | for MCP clients |

Markdown conventions mirror the printed Resolution: defined terms are `**_bold italic_**`, cross-references are
links to the canonical `zr.planning.nyc.gov` section URL, nested paragraphs are `(a)` / `(1)` / `(i)`, tables are
markdown tables, figures are image links.

## Use it: clone and run (no install)

```bash
git clone https://github.com/noah-pantastic/nyc-zr.git ~/nyc-zr   # first time
git -C ~/nyc-zr pull                                             # before a job: amendments arrive as commits
python3 ~/nyc-zr/scripts/zr.py get 23-344                        # the index builds itself on the first run (~5 s)
```

The CLI needs only Python 3.11+ with SQLite FTS5 (python.org, Homebrew and most Linux builds have it). Everything it
reads is in the repo: `data/*.jsonl` is the source of the index, and `data/zr.sqlite` is built from it locally and
gitignored. Only `history` touches the network.

## CLI

```bash
python3 scripts/zr.py search "rear yard corner lot" --district R6B --article II   # --json for machine-readable hits
python3 scripts/zr.py get 23-344 --parents          # one section as markdown with a JSON header; --children for a whole group
python3 scripts/zr.py define "corner lot"           # 12-10 definitions, follows "see …" pointers
python3 scripts/zr.py chapter II 3 | toc [II] | district R6B | history 23-344
```

Every hit and section carries the section id, the official `zr.planning.nyc.gov` URL and the last-amended date, so an
answer can be cited without opening the site.

A project skill at `.claude/skills/zr/SKILL.md` teaches Claude Code the same commands; copy it to
`~/.claude/skills/zr/` to make it available from any directory.

## Rebuild from the source site

```bash
uv sync                                   # bs4, lxml, requests, pyyaml, mcp: only these scripts and the MCP server need them
.venv/bin/python scripts/fetch.py         # ~2 min: 117 pages into raw/ (cached; --force to refetch)
.venv/bin/python scripts/parse.py         # ~20 s: raw/ -> sections/ + data/*.jsonl + data/toc.json
.venv/bin/python scripts/build_index.py   # ~5 s: data/zr.sqlite
```

Re-run all three whenever DCP amends the text (the site shows per-section "Last Amended" dates, which land in the
frontmatter, so `git diff sections/` shows exactly what changed), then commit so clones pick the change up with `git pull`.

## MCP server

Needs the `mcp` package from `uv sync`.

```bash
claude mcp add --scope user nyc-zr -- "$PWD/.venv/bin/python" "$PWD/mcp/server.py"
```

(On a managed Claude Code install `claude mcp add` can be refused by enterprise policy; the CLI above is the
fallback, and the server still works with any other MCP client.)

Tools:

- `zr_search(query, limit, article, chapter, district, scope)` — BM25 full text. Words are AND-ed, `"quoted phrases"`,
  `prefix*`, and FTS5 `AND/OR/NOT/NEAR` work. `district="R6B"` keeps only sections that name that district.
  `scope` = sections | definitions | appendices | all.
- `zr_get_section(id, include_children, include_parents)` — one section as markdown; `include_children=true` returns
  a whole group (e.g. all of 23-30 Yard Regulations).
- `zr_list_chapter(article, chapter)` / `zr_toc(article)` — navigation.
- `zr_define(term)` — 12-10 definitions, general first, then chapter-specific; short aliases work ("accessory").
- `zr_district(district, article)` — every section naming a district token.
- `zr_history(id)` — live amendment history from the DCP site.

## Architecture

```
zr.planning.nyc.gov ──fetch──> raw/*.html ──parse──> sections/*.md + data/*.jsonl
                                                          │
                                             ┌────────────┴────────────┐
                                             ▼                         ▼
                                     data/zr.sqlite (FTS5)        git history
                                             │                 (diff on amendment)
                                scripts/zr.py · mcp/server.py
                                             │
                                             ▼
                                           agent
```

The markdown tree is the source of truth and the git history records what DCP changed. Retrieval over the 5 MB of
legal text goes through the SQLite FTS layer, reached from the CLI or the MCP server, which return the section id,
the official URL and the last-amended date so every answer can be cited.

Source text © City of New York, Department of City Planning. This repo only restructures it.
