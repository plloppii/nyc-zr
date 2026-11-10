# nyc-zr — the NYC Zoning Resolution, made searchable for agents

[![ci](https://github.com/plloppii/nyc-zr/actions/workflows/ci.yml/badge.svg)](https://github.com/plloppii/nyc-zr/actions/workflows/ci.yml)

The full text of the [NYC Zoning Resolution](https://zr.planning.nyc.gov) (14 Articles, 101 chapters,
~4,000 sections, the 12-10 definitions, and the appendices) scraped into:

| Layer | What | Why |
| --- | --- | --- |
| `sections/**.md` | one markdown file per section with YAML frontmatter (id, title, article, chapter, parent, last-amended date, defined terms, cross-refs, districts named, source URL) | grep-able, diff-able, git-friendly source of truth |
| `data/*.jsonl`, `data/toc.json` | the same data as JSON records plus the outline | what the index is built from, so a clone is self-contained |
| `data/meta.json` | provenance: when the pages were fetched, the latest amendment date in the text, counts | so an answer can say what date the text is as of |
| `data/zr.sqlite` | SQLite with FTS5 (BM25) over sections, definitions, appendices; built locally on first use | fast, deterministic search with snippets |
| `scripts/zr.py` | CLI: search / get / define / chapter / toc / district / history / info | what an agent runs; plain `python3`, no install |
| `mcp/server.py` | the same functions as an MCP server | for MCP clients |

Markdown conventions mirror the printed Resolution: defined terms are `**_bold italic_**`, cross-references are
links to the canonical `zr.planning.nyc.gov` section URL, nested paragraphs are `(a)` / `(1)` / `(i)`, tables are
markdown tables, figures are image links. The `districts` list names every district a section mentions, with ranges
such as "R6 through R12" expanded to each district in between.

## Use it: clone and run (no install)

```bash
git clone https://github.com/plloppii/nyc-zr.git ~/nyc-zr   # first time
git -C ~/nyc-zr pull                                             # before a job: amendments arrive as commits
python3 ~/nyc-zr/scripts/zr.py get 23-344                        # the index builds itself on the first run (~1 s)
python3 ~/nyc-zr/scripts/zr.py info                              # what date the text is as of
```

The CLI needs only Python 3.11+ with SQLite FTS5 (python.org, Homebrew and most Linux builds have it). Everything it
reads is in the repo: `data/*.jsonl` is the source of the index, and `data/zr.sqlite` is built from it locally and
gitignored. Only `history` touches the network, and it too needs nothing beyond the standard library.

## CLI

```bash
python3 scripts/zr.py search "rear yard corner lot" --district R6B --article II   # words AND-ed; "phrases", prefix*, AND/OR/NOT/NEAR
python3 scripts/zr.py get 23-344 --parents          # one section as markdown with a JSON header; --children for a whole group
python3 scripts/zr.py define "corner lot"           # 12-10 definitions, follows "see …" pointers
python3 scripts/zr.py chapter II 3 | toc [II] | district R6B | history 23-344 | info
```

- Every command takes `--json` for machine-readable output.
- Errors (`no section 99-999`, bad query syntax, an unknown article) go to stderr with exit status 1; usage errors exit 2.
  `--chapter` requires `--article`, because chapter numbers repeat in every article.
- `--district R6B` keeps sections that name R6B or its family (R6), including those that say "R6 through R12".
- A bare group heading such as `23-00` has no text of its own; `get` lists its subsections so you know what to read next.

Every hit and section carries the section id, the official `zr.planning.nyc.gov` URL and the last-amended date, so an
answer can be cited without opening the site. `info` reports the fetch date and the latest amendment in the text; the
`built_at` time in the index is only when this clone indexed it.

A project skill at `.claude/skills/zr/SKILL.md` teaches Claude Code the same commands; copy it to
`~/.claude/skills/zr/` to make it available from any directory.

## Rebuild from the source site

```bash
uv sync                                   # bs4, lxml, requests, pyyaml, mcp (+ pytest): only these scripts, the MCP server and the tests need them
.venv/bin/python scripts/fetch.py         # ~2 min: 117 pages into raw/ (cached; --force to refetch)
.venv/bin/python scripts/parse.py         # ~20 s: raw/ -> sections/ + data/*.jsonl + data/toc.json + data/meta.json
.venv/bin/python scripts/build_index.py   # ~1 s: data/zr.sqlite
```

`fetch.py` first reads the site's home and article pages and appends any chapter or appendix missing from
`data/chapter_urls.txt` / `data/appendix_urls.txt` (`--no-discover` to skip). `parse.py` rebuilds `sections/` from
scratch, so a section DCP removes or renumbers disappears rather than lingering. Re-run all three whenever DCP amends
the text (the site shows per-section "Last Amended" dates, which land in the frontmatter, so `git diff sections/`
shows exactly what changed), then commit so clones pick the change up with `git pull`.

Plain text for the search index is derived from the markdown at build time (`scripts/zrtext.py`, standard library
only), so the committed JSON carries each section once.

## Tests

```bash
uv run pytest -q
```

Unit tests cover the query parser, district tokens and ranges, the HTML→markdown converter, and the shared search
functions against a small synthetic index; `tests/test_cli.py` runs the real CLI against the committed data. CI runs
the suite and, separately, the CLI on a bare runner with the system `python3` to keep the no-install promise honest.

## MCP server

Needs the `mcp` package from `uv sync`.

```bash
claude mcp add --scope user nyc-zr -- "$PWD/.venv/bin/python" "$PWD/mcp/server.py"
```

(On a managed Claude Code install `claude mcp add` can be refused by enterprise policy; the CLI above is the
fallback, and the server still works with any other MCP client.)

Tools:

- `zr_search(query, limit, article, chapter, district, scope)` — BM25 full text, titles weighted above body text.
  Words are AND-ed, `"quoted phrases"`, `prefix*`, and FTS5 `AND/OR/NOT/NEAR` work; if nothing matches every term,
  the same words and phrases are OR-ed. `district="R6B"` keeps only sections that name that district or its family.
  `chapter` requires `article`. `scope` = sections | definitions | appendices | all.
- `zr_get_section(id, include_children, include_parents)` — one section as markdown; `include_children=true` returns
  a whole group (e.g. all of 23-30 Yard Regulations). A bare heading lists its subsections.
- `zr_list_chapter(article, chapter)` / `zr_toc(article)` — navigation.
- `zr_define(term)` — 12-10 definitions, general first, then chapter-specific; short aliases work ("accessory").
- `zr_district(district, article)` — every section naming a district token, ranges included.
- `zr_history(id)` — live amendment history from the DCP site.

Errors come back as a JSON object with an `error` key.

## Architecture

```
zr.planning.nyc.gov ──fetch──> raw/*.html ──parse──> sections/*.md + data/*.jsonl + data/meta.json
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

## License

The code is MIT licensed (see `LICENSE`). The Resolution text under `sections/` and `data/` is © City of New York,
Department of City Planning; this repo only restructures it.
