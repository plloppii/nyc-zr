# nyc-zr — the NYC Zoning Resolution, made searchable for agents

The full text of the [NYC Zoning Resolution](https://zr.planning.nyc.gov) (14 Articles, 101 chapters,
~4,000 sections, the 12-10 definitions, and the appendices) scraped into:

| Layer | What | Why |
| --- | --- | --- |
| `sections/**.md` | one markdown file per section with YAML frontmatter (id, title, article, chapter, parent, last-amended date, defined terms, cross-refs, districts mentioned, source URL) | grep-able, diff-able, git-friendly source of truth |
| `data/*.jsonl`, `data/toc.json` | the same data as JSON records plus the outline | feed any index or sync |
| `data/zr.sqlite` | SQLite with FTS5 (BM25) over sections, definitions, appendices | fast, deterministic search with snippets |
| `mcp/server.py` | MCP server exposing search / get_section / define / toc / history | what the agent actually calls |
| `scripts/notion_sync.py` | mirrors every section and definition into two Notion databases | browsable in Notion; other pages can relate to section rows |

Markdown conventions mirror the printed Resolution: defined terms are `**_bold italic_**`, cross-references are
links to the canonical `zr.planning.nyc.gov` section URL, nested paragraphs are `(a)` / `(1)` / `(i)`, tables are
markdown tables, figures are image links.

## Build

```bash
uv sync                                   # python deps (bs4, lxml, requests, pyyaml, mcp)
.venv/bin/python scripts/fetch.py         # ~2 min: 117 pages into raw/ (cached; --force to refetch)
.venv/bin/python scripts/parse.py         # ~20 s: raw/ -> sections/ + data/*.jsonl + data/toc.json
.venv/bin/python scripts/build_index.py   # ~5 s: data/zr.sqlite
```

Re-run all three whenever DCP amends the text (the site shows per-section "Last Amended" dates, which land in the
frontmatter, so `git diff sections/` shows exactly what changed).

## CLI (works anywhere, including Claude Code via Bash)

```bash
.venv/bin/python scripts/zr.py search "rear yard corner lot" --district R6B --article II
.venv/bin/python scripts/zr.py get 23-344 --parents          # one section; --children for a whole group
.venv/bin/python scripts/zr.py define "corner lot"           # 12-10 definitions, follows "see …" pointers
.venv/bin/python scripts/zr.py chapter II 3 | toc [II] | district R6B | history 23-344
```

A project skill at `.claude/skills/zr/SKILL.md` teaches Claude Code the same commands; copy it to
`~/.claude/skills/zr/` to make it available from any directory.

## MCP server

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

## Notion mirror

Live copies (workspace "Noah Pan's Notion", under the **DOB** page, loaded 2026-10-14):

- Sections: https://app.notion.com/p/3f99d8ce4a7481539aabc2d09cb6ca5b
- Definitions: https://app.notion.com/p/3f99d8ce4a7481248759d991c0770239

```bash
export NOTION_TOKEN=secret_...            # or NOTION_API_TOKEN; internal integration shared with the parent page
export NOTION_PARENT_PAGE_ID=<page id>    # where the two databases get created (first run only; ids then live in data/notion_state.json)
.venv/bin/python scripts/notion_sync.py --sections --article II --chapter 3 --dry-run   # inspect
.venv/bin/python scripts/notion_sync.py --sections --definitions                        # everything (~1 h at 3 req/s)
```

Creates **NYC Zoning Resolution — Sections** (Name, Section ID, Article, Chapter, Level, Parent, Last Amended,
Districts, Defined Terms, Cross Refs, URL, Node ID) with the section text as the page body, and
**NYC Zoning Resolution — Definitions**. `data/notion_state.json` tracks page ids and content hashes so re-runs only
push changed sections (old page archived, new one created); `--force` re-pushes regardless. A full load is about
4,500 pages and takes roughly 70 minutes at Notion's rate limit. Inline base64 figures (8 sections) become a text
placeholder because Notion only accepts http image URLs; commas in Chapter names become semicolons because Notion
forbids commas in select options.

## Using the Notion mirror from another agent

`docs/AGENT_GUIDE.md` is the instruction set for any agent that reaches the databases through Notion (Notion AI,
the hosted Notion MCP, an iMessage assistant, …): schema, exact filter recipes, the search pitfalls (the Notion
search endpoint is title-only and fuzzy-matches section numbers; use a `Section ID equals` filter instead), and a
paste-ready instruction block. `scripts/notion_guide.py` publishes it as a page beside the databases and writes a
pointer into each database's description, so an agent that fetches the database sees where to look:

- Guide page: https://www.notion.so/3fa9d8ce4a748119be68f1820e9b355d
- Data source ids (API 2025-09-03): Sections `3f99d8ce-4a74-812b-abfd-000b4d75dcfa`, Definitions `3f99d8ce-4a74-81ea-af83-000be9038f2d`

Re-run the script after editing the guide; it updates the page in place.

## Architecture

```
zr.planning.nyc.gov ──fetch──> raw/*.html ──parse──> sections/*.md + data/*.jsonl
                                                          │
                                   ┌──────────────────────┼──────────────────────┐
                                   ▼                      ▼                      ▼
                           data/zr.sqlite (FTS5)   Notion databases        git history
                                   │                      │
                             mcp/server.py         Notion MCP / UI
                                   │                      │
                                   └────────► agent ◄─────┘
                               (retrieval here)   (notes, analyses, relations here)
```

Notion is the workspace, not the search engine: its API search is title-only and its content search is fuzzy, so
retrieval over 5 MB of legal text goes through the SQLite/MCP layer, and Notion holds the browsable mirror plus the
agent's own analyses, which link to section rows and to the official URLs.

Source text © City of New York, Department of City Planning. This repo only restructures it.
