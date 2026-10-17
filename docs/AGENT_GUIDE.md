# README for agents — NYC Zoning Resolution databases

Read this before using the two databases next to this page. They are a **read-only mirror** of the full text of the New York City Zoning Resolution (ZR), published by the Department of City Planning (DCP) at https://zr.planning.nyc.gov. Snapshot taken 2026-10-14; the newest amendment in the text is dated 2026-07-16. The official site is authoritative when anything depends on exact wording.

## What is here

| Database | Rows | Database id | Data source id | One row is |
| --- | --- | --- | --- | --- |
| NYC Zoning Resolution — Sections | 4,051 | `3f99d8ce-4a74-8153-9aab-c2d09cb6ca5b` | `3f99d8ce-4a74-812b-abfd-000b4d75dcfa` | one numbered section (e.g. 23-344); the page body is its full text |
| NYC Zoning Resolution — Definitions | 483 (442 distinct terms) | `3f99d8ce-4a74-8124-8759-d991c0770239` | `3f99d8ce-4a74-81ea-af83-000be9038f2d` | one defined term from ZR 12-10 or a special-district chapter; the page body is the definition |

- Sections: https://app.notion.com/p/3f99d8ce4a7481539aabc2d09cb6ca5b
- Definitions: https://app.notion.com/p/3f99d8ce4a7481248759d991c0770239

Use the **data source id** with `POST /v1/data_sources/{id}/query` (Notion-Version 2025-09-03) or with the hosted Notion MCP tool `notion-query-data-sources`. Older API versions take the database id with `POST /v1/databases/{id}/query`. The filter objects are identical.

## Ground rules

1. Never edit, move, archive or delete rows. A sync script regenerates them; a changed section is archived and re-created, so **page ids are not stable**. Store Section IDs (e.g. `23-344`), never page ids.
2. Cite as `Section ID + title`, give the `URL` property (the official page) and the `Last Amended` date, and quote numbers (FAR, feet, percentages) verbatim from the page body. Do not round or paraphrase a numeric rule.
3. Put your own analysis in your own pages (a deal page, a memo) and link to the section rows from there.
4. A section rarely stands alone. After finding one, read its siblings (same `Parent`) and the `Cross Refs` it names before concluding.
5. If a question turns on the exact current text, open the `URL` and confirm. The mirror is a snapshot.

## Sections — properties

| Property | Type | Meaning / exact format |
| --- | --- | --- |
| Name | title | `"<Section ID> <Title>"`, e.g. `23-344 Additional rear yard modifications` |
| Section ID | text | `23-344`. Pattern: two or three digits, hyphen, two to four digits |
| Article | select | Roman numeral, em dash, title: `II — Residence District Regulations` … `XIV — Special Purpose Districts` |
| Chapter | select | `II-3 Residential Bulk Regulations in Residence Districts`. Commas in official names are semicolons here |
| Level | number | 1 = chapter division (`23-30`), 2 = section (`23-34`), 3 = subsection (`23-344`) |
| Parent | text | Section ID of the parent row; empty for Level 1 |
| Last Amended | date | DCP's "Last Amended" date; empty for 6 sections where DCP shows none |
| Districts | multi-select | zoning-district tokens literally named in the text (`R6B`, `C4-4A`, `M1-5`, …). About 2,800 general sections name none |
| Defined Terms | text | comma-separated 12-10 terms used in the body (`corner lot, rear yard, zoning lot`) |
| Cross Refs | text | comma-separated Section IDs the body links to |
| URL | url | official page, `https://zr.planning.nyc.gov/article-ii/chapter-3/23-344` |
| Node ID | number | DCP internal id. Ignore |
| Hash | text | sync bookkeeping. Ignore |

The page body is the section text. Defined terms are **bold italic**, nested paragraphs are `(a)` / `(1)` / `(i)`, tables are real Notion tables, figures are images or a `[figure: …]` placeholder.

## Definitions — properties

| Property | Type | Meaning / exact format |
| --- | --- | --- |
| Term | title | lower-case as printed in 12-10: `corner lot`, `yard, rear`, `floor area` |
| Scope | select | `general` (applies everywhere) or `chapter` (special-district or chapter-specific meaning) |
| Applicability | text | for `chapter` scope: the chapter or district the meaning applies to |
| Source Section | text | `12-10`, or the special-district section that defines it |
| Last Amended | date | |
| URL | url | official page |
| Key, Hash | text | bookkeeping. Ignore |

A body that reads "see **other term**" means the definition lives under that other term; look it up.

## How to find things

1. **You have a section number.** Filter Sections: `Section ID equals "23-344"`. Do **not** use workspace search for numbers: it fuzzy-matches (searching `23-344` returns 146-23, 145-23, 144-23 …).
2. **You need a definition.** Filter Definitions: `Term equals "corner lot"`, or `Term contains "yard"` to list variants. Prefer `Scope = general` unless you are inside the chapter named in `Applicability`. Many terms are indexed noun-first (`yard, rear`), so try both `rear yard` and `yard, rear`.
3. **You have a topic.** Filter Sections: `Name contains "rear yard"` (titles are descriptive), then read the bodies. `Defined Terms contains "corner lot"` finds every section that uses the term (75 rows). Workspace search through the API is **title-only**; if your client has Notion AI search (`notion-search` with a natural-language question, location limited to the Sections data source) it can search bodies, capped at 50 results.
4. **You have a zoning district.** `Districts contains "R6B"`. Also query the family, because many rules are written for the parent district: `R6B → R6`; `C4-4A → C4-4, C4`; `M1-5 → M1`. Rules that apply to "all districts" carry no token at all, so a district filter narrows a search; it never proves a rule is absent. For a complete answer, combine the district filter with a `Chapter` filter, then also read that chapter's general rows (Level 1 and 2 with empty `Districts`).
5. **Walk the outline.** `Parent equals "23-34"` lists the subsections. `Chapter equals "II-3 …"` with `Level = 1` gives the chapter's divisions. Sort by `Section ID` ascending.
6. **What changed recently.** `Last Amended on_or_after 2025-01-01`, sort descending.
7. **Amendment history** (ULURP numbers, CPC reports) is not mirrored. The official page's History tab has it; open the `URL`.

## Where things live (chapter map)

| Question | Look in |
| --- | --- |
| What does a term mean | Definitions database (ZR 12-10) |
| What uses are allowed in R / C / M districts | Chapters II-2, III-2, IV-2 |
| Residential FAR, lot coverage, yards, height and setback | Chapter II-3 (R districts); Chapter III-5 / IV-3 for residential in C and M districts |
| Community facility bulk | Chapter II-4, III-3 |
| Parking and loading | Chapters II-5, III-6, IV-4; Chapter I-3 for the Manhattan Core; I-6 for Long Island City |
| Non-conforming uses, non-complying buildings | Article V |
| Waterfront, flood resilience | Chapters VI-2 and VI-4 |
| Special permits, authorizations, variances (BSA / CPC) | Article VII |
| Special purpose districts (named areas) | Articles VIII through XIV, one chapter per district |

The full list of 101 chapters is the `Chapter` select's option list; fetch the data source schema to see it.

## Query recipes

Filter objects for `POST /v1/data_sources/3f99d8ce-4a74-812b-abfd-000b4d75dcfa/query` (Sections) and `…/3f99d8ce-4a74-81ea-af83-000be9038f2d/query` (Definitions). The same objects go in the `filter` field of `notion-query-data-sources` (rows mode).

Exact section:

```json
{"filter": {"property": "Section ID", "rich_text": {"equals": "23-344"}}}
```

Subsections of a section:

```json
{"filter": {"property": "Parent", "rich_text": {"equals": "23-34"}},
 "sorts": [{"property": "Section ID", "direction": "ascending"}]}
```

District within a chapter:

```json
{"filter": {"and": [
  {"property": "Districts", "multi_select": {"contains": "R6B"}},
  {"property": "Chapter", "select": {"equals": "II-3 Residential Bulk Regulations in Residence Districts"}}
]}}
```

Every section that uses a defined term, or whose title mentions a topic:

```json
{"filter": {"or": [
  {"property": "Defined Terms", "rich_text": {"contains": "corner lot"}},
  {"property": "Name", "title": {"contains": "corner lot"}}
]}}
```

A definition:

```json
{"filter": {"and": [
  {"property": "Term", "title": {"equals": "corner lot"}},
  {"property": "Scope", "select": {"equals": "general"}}
]}}
```

Recently amended, newest first:

```json
{"filter": {"property": "Last Amended", "date": {"on_or_after": "2025-01-01"}},
 "sorts": [{"property": "Last Amended", "direction": "descending"}]}
```

Then read the body: `GET /v1/blocks/{page_id}/children` (paginate; tables have their own children), or `notion-fetch` with the row's URL.

## Known gaps

- Snapshot of 2026-10-14. Re-synced when DCP amends text; check `Last Amended` against the official page when it matters.
- Six sections have no `Last Amended` date because DCP's site shows none: 23-739, 35-80, 36-441, 105-426, 113-13, 135-30.
- Eight sections had figures embedded inline on DCP's site; here they appear as `[figure: …]` text. Open the `URL` to see them.
- Bodies average about 1,300 characters but tables can push a section past 50,000. Read selectively.
- The `Districts` property comes from tokens in the text, so a section that says "R6 through R10" is tagged `R6` and `R10` only.

## Paste-ready instruction block for another agent

```
NYC ZONING LOOKUPS. Noah's Notion holds the full NYC Zoning Resolution as two read-only databases under Real Estate → NYC Zoning & Building Code:
- Sections (data source 3f99d8ce-4a74-812b-abfd-000b4d75dcfa): one row per section, title "23-344 Additional rear yard modifications", full text in the page body. Properties: Section ID, Article, Chapter, Level (1 division / 2 section / 3 subsection), Parent, Last Amended, Districts, Defined Terms, Cross Refs, URL.
- Definitions (data source 3f99d8ce-4a74-81ea-af83-000be9038f2d): one row per defined term (ZR 12-10), title = term, body = definition. Properties: Scope (general|chapter), Applicability, Source Section, URL.
Before the first lookup read the page "README for agents — NYC Zoning Resolution databases" next to them.
How to search: section number → filter Section ID equals "<id>" (never workspace search; it fuzzy-matches numbers). Term → filter Term equals "<term>" (try "yard, rear" and "rear yard"). Topic → Name contains "<words>" or Defined Terms contains "<term>". District → Districts contains "R6B", plus the family token "R6"; general rules carry no district token, so also read the chapter's Level 1–2 rows. Subsections → Parent equals "<id>". Then read the page body.
Rules: never edit rows; store Section IDs not page ids; cite Section ID + title + URL property + Last Amended; quote numeric limits verbatim from the body; read siblings and Cross Refs before concluding; zr.planning.nyc.gov is authoritative.
```
