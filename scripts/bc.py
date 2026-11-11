#!/usr/bin/env python3
"""Fetch and parse the 2022 NYC Building Code into data/bc/ and sections/bc/.

The Department of Buildings publishes the code as one PDF per chapter and per appendix (the online HTML editions sit
behind browser challenges). Each PDF is downloaded from the DOB index page, read with pdftotext, and split into
sections (SECTION BC 1208) and numbered subsections (1208.1, 1208.1.1 ...), each becoming an entry the CLI can cite as
`bc:1208.1`. Sections marked ‡ in the PDF carry the Local Law and effective date named at the top of the chapter.
A table stays with the subsection that introduces it, as its caption followed by one fixed-width block, so that the
search index can leave the rows out and rank the prose.

  .venv/bin/python scripts/bc.py [--only 12 | --only D] [--force]       needs pdftotext: brew install poppler

The 34 chapter PDFs the DOB page lists are read (it has no chapter 33), plus the appendices named in APPENDICES.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lawkit import Fetcher, http_date, nice_title, norm, now, parse_date, pdf_pages, reflow, write_jsonl, write_md  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "raw/bc"
DATA = ROOT / "data/bc"
OUT = ROOT / "sections/bc"
INDEX = "https://www.nyc.gov/site/buildings/codes/2022-construction-codes.page"
PDF_BASE = "https://www.nyc.gov/assets/buildings/codes-pdf/cons_codes_2022/"

# Appendices read: D is the fire districts (which construction types may be built where). The other appendices DOB
# publishes are not read: E supplementary accessibility, G flood-resistant construction, K elevator standards,
# M one- and two-family dwellings, N assistive listening, Q fire standards, R acoustical tile, S exit path markings,
# U ancillary dwelling units (A, B, C, I, J, L, O, P are reserved).
APPENDICES = ("D",)

FILE_RE = re.compile(r"file=(2022BC_(?:Chapter(\d{2})|Appendix([A-Z]))_[^&\"']+?\.pdf)")
NOTE_RE = re.compile(r"^\s*(‡+)\s*Chapter \d+ was amended by (.+?)\. This law has an effective date of (.+?)\.")
SECTION_RE = re.compile(r"^\s*SECTION BC ([A-Z]?\d{3,4})\s*$")
SUB_RE = re.compile(r"^\s*(‡+\s*)?([A-Z]?\d{3,4}(?:\.\d+)+)\s+([A-Z].*?\.)(?:\s+(.*))?$")
TABLE_RE = re.compile(r"^\s*TABLE [A-Z]?\d{3,4}")
COLUMNS_RE = re.compile(r"\S\s{3,}\S")  # a layout row: cells separated by runs of spaces
PARA_START = re.compile(r"^(\d+\.|\d+(?:\.\d+)+\.?|[a-z]\.|\([a-z0-9]+\)|•)\s|^(Exceptions?:|Notes?:)(\s|$)"
                        r"|^[A-Z][A-Z0-9][A-Z0-9 ,/()'’-]*\.(\s|$)")  # a defined TERM. in Chapter 2
MARK_RE = re.compile(r"‡+\s*")


def unit_key(unit: str) -> int:
    """Chapters sort by number and appendices after them by letter: '5' -> 5, 'D' -> 104."""
    return int(unit) if unit.isdigit() else 100 + ord(unit) - ord("A") + 1


def sort_key(unit: str, sid: str | None) -> str:
    if sid is None:
        return f"{unit_key(unit):03d}"
    return f"{unit_key(unit):03d}." + ".".join(f"{int(p):04d}" for p in sid.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").split("."))


def in_unit(sid: str, unit: str) -> bool:
    """1208.1 belongs to chapter 12; D102.2 to appendix D."""
    if unit.isdigit():
        return sid[:1].isdigit() and int(sid.split(".")[0]) // 100 == int(unit)
    return sid.startswith(unit)


def heading_id(unit: str) -> str:
    return f"chapter-{unit}" if unit.isdigit() else f"appendix-{unit.lower()}"


def parse_unit(unit: str, pages: list[str], pdf_url: str, doc_date: str | None) -> tuple[list[dict], dict]:
    """One chapter ('12') or appendix ('D') PDF -> [heading, section, subsection, ...], heading."""
    notes: dict[str, tuple[str, str | None]] = {}  # ‡‡‡ -> (Local Law, effective date)
    unit_title = None
    entries: list[dict] = []
    cur: dict | None = None
    prose: list[str] = []
    tables: list[list[list[str]]] = []  # [caption lines, row lines] per table in the current entry
    mode = "prose"  # "caption" / "rows" while inside a table
    pending_section: dict | None = None  # SECTION BC xxxx seen, title lines follow
    head_re = re.compile(rf"^(CHAPTER|APPENDIX) {unit}$")
    placed = f"Ch {unit}" if unit.isdigit() else f"App {unit}"

    def close():
        nonlocal cur, prose, tables, mode
        if cur is not None:
            body = reflow(prose, PARA_START)
            for caption, rows in tables:
                while rows and not rows[-1].strip():
                    rows.pop()
                body = (body + "\n\n" if body else "") + " ".join(caption)
                if rows:
                    body += "\n\n```\n" + "\n".join(rows) + "\n```"
            cur["body_md"] = body
            entries.append(cur)
        cur, prose, tables, mode = None, [], [], "prose"

    def add_line(line: str, s: str):
        nonlocal mode
        if TABLE_RE.match(s):
            tables.append([[s], []])
            mode = "caption"
        elif mode == "caption" and s and s.isupper() and not COLUMNS_RE.search(line):
            tables[-1][0].append(s)  # the caption continues in capitals
        elif mode in ("caption", "rows"):
            mode = "rows"
            tables[-1][1].append(line)
        else:
            prose.append(line)

    for pno, page in enumerate(pages, 1):
        lines = page.split("\n")
        page_has_text = False
        for i, raw in enumerate(lines):
            line = raw.rstrip()
            s = line.strip()
            if not s:
                if cur is not None:
                    add_line("", "")
                continue
            first_on_page, page_has_text = not page_has_text, True
            m = NOTE_RE.match(s)
            if m:
                notes[m.group(1)] = (m.group(2), parse_date(m.group(3)))
                continue
            if unit_title is None:
                if head_re.match(s):
                    # the title is the next non-empty line(s)
                    rest = [x.strip() for x in lines[i + 1:] if x.strip()]
                    unit_title = nice_title(rest[0]) if rest else f"{'Chapter' if unit.isdigit() else 'Appendix'} {unit}"
                continue
            if (s.upper() == unit_title.upper() and (pending_section is None or first_on_page)) \
                    or re.fullmatch(r"\d{1,4}", s) or "NEW YORK CITY BUILDING CODE" in s:
                continue  # running header, page number, footer
            m = SECTION_RE.match(s)
            if m and in_unit(m.group(1), unit):
                close()
                pending_section = {"id": m.group(1), "title_lines": [], "page": pno}
                continue
            if pending_section is not None:
                if s.isupper() and not SUB_RE.match(s) and not TABLE_RE.match(s):
                    pending_section["title_lines"].append(s)
                    continue
                sid = pending_section["id"]
                cur = {"id": sid, "title": nice_title(" ".join(pending_section["title_lines"])) or f"Section {sid}",
                       "level": 2, "parent": heading_id(unit), "url": f"{pdf_url}#page={pending_section['page']}",
                       "last_amended": None, "place": placed, "sort_key": sort_key(unit, sid)}
                prose, tables, mode = [], [], "prose"
                pending_section = None
            m = SUB_RE.match(s)
            if m and in_unit(m.group(2), unit):
                close()
                marks, sid, title, rest = m.group(1), m.group(2), m.group(3), m.group(4) or ""
                parent = sid.rsplit(".", 1)[0]
                law, date = notes.get((marks or "").strip(), (None, None))
                cur = {"id": sid, "title": title.rstrip("."), "level": 2 + sid.count("."), "parent": parent,
                       "url": f"{pdf_url}#page={pno}", "last_amended": date, "amended_by": law,
                       "place": placed, "sort_key": sort_key(unit, sid)}
                prose = [rest] if rest else []
                continue
            if cur is not None:
                add_line(MARK_RE.sub("", line) if "‡" in line else line, MARK_RE.sub("", s))
    close()
    seen: set[str] = set()
    for e in entries:
        if e["id"] in seen:
            print(f"    note: {e['id']} appears twice in the {unit} PDF; the first is kept", file=sys.stderr)
        seen.add(e["id"])
    kind = "Chapter" if unit.isdigit() else "Appendix"
    heading = {"id": heading_id(unit), "title": f"{kind} {unit}: {unit_title or ''}".strip(), "level": 1, "parent": None,
               "url": pdf_url, "last_amended": None, "place": "", "body_md": "", "sort_key": sort_key(unit, None),
               "notes": [{"local_law": law, "effective": date} for law, date in notes.values()], "document_date": doc_date}
    return [heading, *entries], heading


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="one chapter number or appendix letter (for a quick check)")
    ap.add_argument("--force", action="store_true", help="re-download PDFs already cached under raw/bc/")
    a = ap.parse_args()
    only = a.only.strip().upper().lstrip("0") if a.only else None

    f = Fetcher(RAW, delay=0.7, force=a.force)
    index = f.get(INDEX, "index")
    files: dict[str, str] = {}
    for m in FILE_RE.finditer(index):
        name, ch, apx = m.groups()
        if ch:
            files[str(int(ch))] = name
        elif apx in APPENDICES:
            files[apx] = name
    n_chapters = sum(1 for u in files if u.isdigit())
    print(f"{n_chapters} chapter PDFs and {len(files) - n_chapters} appendix PDFs to read from the DOB page")
    if not only and OUT.exists():
        shutil.rmtree(OUT)

    all_entries: list[dict] = []
    latest = ""
    for unit in sorted(files, key=unit_key):
        if only and unit != only:
            continue
        name = files[unit]
        pdf, info = f.download(PDF_BASE + name, name)
        doc_date = http_date(info.get("last_modified"))
        pages = pdf_pages(pdf)
        entries, heading = parse_unit(unit, pages, PDF_BASE + name, doc_date)
        latest = max([latest, *(e["last_amended"] or "" for e in entries)])
        n_sec = sum(1 for e in entries if e["level"] == 2)
        print(f"  {unit:>3} {heading['title'][:45]:<46} {n_sec:3} sections {len(entries) - 1 - n_sec:4} subsections  pdf {doc_date}")
        all_entries.extend(entries)
        # one markdown file per section, its subsections inside
        for sec in (e for e in entries if e["level"] == 2):
            subs = [e for e in entries if e["level"] > 2 and (e["id"] + ".").startswith(sec["id"] + ".")]
            body = "\n\n".join(f"{'#' * min(6, e['level'])} {e['id']} {e['title']}"
                               + (f"\n_Amended by {e['amended_by']}, effective {e['last_amended']}_" if e.get("amended_by") else "")
                               + f"\n\n{e['body_md']}" for e in subs)
            front = {"id": sec["id"], "title": sec["title"], "code": "NYC Building Code (2022)"}
            if unit.isdigit():
                front.update({"chapter": int(unit), "chapter_title": heading["title"]})
            else:
                front.update({"appendix": unit, "appendix_title": heading["title"]})
            front.update({"url": sec["url"], "pdf_date": doc_date})
            write_md(OUT / heading_id(unit) / f"{sec['id']}.md", front, f"# BC {sec['id']} {sec['title']}\n\n{body}")

    if only:
        print(f"--only: {len(all_entries)} entries parsed, nothing written to data/")
        for e in all_entries[:12]:
            print(f"   {e['id']:<14} L{e['level']} parent={str(e['parent']):<12} {e['title'][:40]:<41} {len(e['body_md']):5} chars  {e.get('last_amended') or ''}")
        return
    n = write_jsonl(DATA / "sections.jsonl", all_entries)
    apx = ", ".join(f"Appendix {u}" for u in files if not u.isdigit())
    meta = {"source": INDEX,
            "edition": f"2022 NYC Building Code: the {n_chapters} chapter PDFs DOB publishes (there is no chapter 33 among them)"
                       + (f" and {apx} (fire districts); the other appendices are not included" if apx else "; appendices not included"),
            "fetched_at": f.first_fetch_at or now(), "latest_amendment": latest or None, "entries": n,
            "chapters": n_chapters, "appendices": [u for u in files if not u.isdigit()],
            "sections": sum(1 for e in all_entries if e["level"] == 2)}
    (DATA / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {n} entries to data/bc/; latest local-law effective date in the text {latest}")


if __name__ == "__main__":
    main()
