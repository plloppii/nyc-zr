# nyc-zr

NYC building law from the command line. The full text of four laws as markdown files, and one small tool that
searches them and routes a floorplan question to the sections that govern it.

| Code | Law | What it governs | Text from |
| --- | --- | --- | --- |
| `zr` | [NYC Zoning Resolution](https://zr.planning.nyc.gov) | bulk and use at the lot level: floor area, height, yards, courts, lot coverage, parking, what counts as floor area, non-complying buildings | zr.planning.nyc.gov |
| `bc` | 2022 NYC Building Code: the 34 chapters DOB publishes, plus Appendix D (fire districts) | construction: occupancy, egress, fire separation, light and ventilation, room dimensions, accessibility, rooftop structures | the Department of Buildings' chapter PDFs |
| `mdl` | NY Multiple Dwelling Law | state law for buildings with three or more units: room sizes, windows, yards and courts, stairs, basements and cellars, conversions | nysenate.gov text, read via newyork.public.law |
| `hmc` | NYC Housing Maintenance Code | minimum standards for existing dwellings: occupancy, room sizes, light and ventilation, sanitary facilities, cellars | the city's PDF of Administrative Code title 27, chapter 2 |

```bash
git clone https://github.com/plloppii/nyc-zr.git
cd nyc-zr
python3 zr.py topic "room size"            # which sections of which law govern it, with citations
python3 zr.py get mdl:31                   # read one
python3 zr.py search "ceiling height" --code bc
```

Needs Python 3.11 or newer, nothing else. The search index builds itself from `data/` on the first run (a few
seconds) and again whenever the data changes, for example after `git pull`.

## Commands

| Command | What it does |
| --- | --- |
| `topic` | lists the floorplan topics: room size, ceiling height, light and ventilation, egress, bathrooms and kitchens, cellars and basements, occupancy, floor area, existing buildings, accessibility, fire separation, rooftop structures |
| `topic "room size"` | the sections across all four laws that govern it, each with a one-line reason, the official URL and the command that opens it. `--text` prints their full text. |
| `search "rear yard corner lot"` | full-text search across every loaded law, best matches first. `--code bc` for one law. Zoning filters: `--district R6B`, `--article II`, `--article II --chapter 3`, `--scope all` to include 12-10 definitions and appendices. Words are AND-ed and stemmed (bulkhead finds bulkheads); `"quoted phrases"`, `prefix*` and `AND / OR / NOT / NEAR` work. When the query is a defined term, the laws' definitions print above the hits. Sections with the same title in one law (the Multiple Dwelling Law repeats a rule for each dwelling class) collapse into one row that lists the others. |
| `get 23-344`, `get bc:1208.1`, `get mdl:31`, `get hmc:27-2074` | one section as markdown. Bare ids are the Zoning Resolution; the others take a `code:` prefix. `--parents` shows the headings above it, `--children` everything under it (a whole group such as `23-30` or `bc:1208`). `get bc:D101.1` reads Appendix D. |
| `define "corner lot"` | definitions from Zoning Resolution Section 12-10. |
| `toc`, `toc II`, `chapter II 3` | the Zoning Resolution outline: all articles, one article, one chapter. |
| `toc mdl`, `toc bc:chapter-10` | another law's headings, or everything under one heading. |
| `district R6B` | every Zoning Resolution section that names a district, including ranges such as "R6 through R12". |
| `history 23-344` | a Zoning Resolution section's amendment history, fetched live from the DCP site. The only command that uses the network. |
| `info` | which laws are loaded, where each came from and what date each is as of. |

Add `--json` to any command for machine-readable output. Every section and search hit carries its citation
(`ZR § 23-344`, `BC 1208.1`, `MDL § 31`, `HMC § 27-2074`), the source URL and the amendment date where the source
records one. Errors go to stderr with exit status 1.

The topics are a hand-kept map in `data/topics.json`: a question, and for each law the sections that answer it with a
one-line reason. The tool checks each citation against the text when it builds the index and says so when one is
missing. It points at the law to read; it does not decide whether a plan complies.

Zoning Resolution ids encode their place: `23-344` is Article II, Chapter 3, group 23-30, section 23-34. Building
Code ids are the code's own (`1208.1` is in Chapter 12). Multiple Dwelling Law sections are numbered through the whole
law (`31`); Housing Maintenance Code sections carry the Administrative Code number (`27-2074`).

## Applicability and currency

Each law reaches a different set of buildings. Before quoting the Multiple Dwelling Law or the Housing Maintenance
Code, say whether it applies to the building at hand, and name any law that does not.

- The Multiple Dwelling Law covers multiple dwellings only: three or more families living independently (MDL § 4).
  One- and two-family houses are outside it. Within it, provisions turn on the class (class A, permanent residence;
  class B, transient) and on when the building was erected: old-law and new-law tenements and converted dwellings
  predate April 18, 1929, and Article 3 governs multiple dwellings erected after that date. Read the applicability
  sentence of each section.
- The Housing Maintenance Code (Administrative Code title 27, chapter 2) applies to all dwellings in the city,
  one- and two-family houses included (HMC § 27-2003), but many sections distinguish multiple dwellings from private
  dwellings and class A from class B.
- The Building Code governs new construction and alterations. Whether an existing building must meet it, or may stay
  under the code it was built to, is set by Administrative Code title 28, which is not included here.
- The Zoning Resolution applies by lot and district; a building that predates the current rules may be non-complying
  (`topic "existing buildings"`).

The text is a snapshot. `info` shows when each law was fetched and its latest recorded amendment; each section
carries a last-amended date where the source records one. Give that date when it matters, and `git pull` before
answering anything date-sensitive. For the Zoning Resolution, `history <id>` fetches a section's amendment history
live.

Not included: the Plumbing Code (fixture counts), the Mechanical Code, the Fire Code, the Energy Conservation Code,
Administrative Code title 28, Building Code chapter 33 (not on the DOB page) and the Building Code appendices other
than D, and the zoning maps.

## What is in the repo

| Path | Contents |
| --- | --- |
| `zr.py` | the tool, standard library only |
| `sections/zr/<article>/<chapter>/<id>.md` | one file per Zoning Resolution section with YAML frontmatter: id, title, parent, last-amended date, defined terms, cross-references, districts named, source URL; `sections/zr/appendices/` holds the appendices |
| `sections/bc/chapter-<n>/<section>.md`, `sections/bc/appendix-d/` | one file per Building Code section (`1208.md`), its subsections inside, with the chapter PDF's date |
| `sections/mdl/article-<n>/[title-<n>/]<section>.md` | one file per Multiple Dwelling Law section with the official nysenate.gov URL and update date |
| `sections/hmc/subchapter-<n>/article-<n>/<section>.md` | one file per Housing Maintenance Code section |
| `data/<code>/sections.jsonl`, `meta.json` | the same text as JSON lines (headings included, with an empty body), and when it was fetched; `data/zr/` also has `definitions.jsonl`, `appendices.jsonl` and `toc.json` |
| `data/topics.json` | the topic map |
| `scripts/` | the scrapers, and `check.py` |

Zoning Resolution markdown mirrors the printed text: defined terms are `**_bold italic_**`, cross-references link to
the canonical section URL, nested paragraphs are `(a)` / `(1)` / `(i)`, tables are markdown tables. Building Code and
Housing Maintenance Code text comes from PDFs, so their tables are kept as a caption and one fixed-width block under
the subsection that introduces them; the search index reads the prose and skips the blocks. `git log -- sections/`
is the amendment history of everything.

## Refresh the text

```bash
uv sync                                   # or: pip install beautifulsoup4 lxml requests pyyaml
brew install poppler                      # pdftotext, for the two PDF sources
.venv/bin/python scripts/zr_fetch.py      # ~2 min: the Zoning Resolution's chapter and appendix pages into raw/zr/
.venv/bin/python scripts/zr_parse.py      # ~20 s: raw/zr/ -> sections/zr/ and data/zr/
.venv/bin/python scripts/bc.py            # ~1 min: the 34 chapter PDFs and Appendix D -> sections/bc/ and data/bc/
.venv/bin/python scripts/mdl.py           # ~4 min: every section page -> sections/mdl/ and data/mdl/
.venv/bin/python scripts/hmc.py           # ~10 s: one PDF -> sections/hmc/ and data/hmc/
git diff --stat sections/                 # what changed
python3 scripts/check.py                  # ranking, topic citations, definition links, one section per law
```

Downloads are cached under `raw/` and only missing files are fetched; `--force` refetches. Commit the result and
clones pick it up with `git pull`, rebuilding their index on the next run.

Provenance differs by source. The Zoning Resolution site gives a last-amended date per section. The Multiple
Dwelling Law mirror gives the state's update date per section. The Building Code PDFs mark amended sections with ‡
and name the Local Law and its effective date at the top of each chapter; other sections carry no date beyond the
PDF's own. The Housing Maintenance Code PDF has no amendment notes at all, so its entries carry only the PDF's date.
`info` reports all of this.

Source text © City of New York (Zoning Resolution, Building Code, Housing Maintenance Code) and State of New York
(Multiple Dwelling Law). This repo only restructures it. Code: MIT.
