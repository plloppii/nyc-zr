#!/usr/bin/env python3
"""Fetch and parse the 2022 NYC Building Code into data/bc/ and sections/bc/.

The Department of Buildings publishes the code as one PDF per chapter (the online HTML editions sit behind browser
challenges). Each chapter PDF is downloaded from the DOB index page, read with pdftotext, and split into sections
(SECTION BC 1208) and numbered subsections (1208.1, 1208.1.1 ...), each becoming an entry the CLI can cite as
`bc:1208.1`. Sections marked ‡ in the PDF carry the Local Law and effective date named at the top of the chapter.

  .venv/bin/python scripts/bc.py [--only 12] [--force]       needs pdftotext: brew install poppler

The 34 chapter PDFs the DOB page lists are read (it has no chapter 33); the appendices are not.
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

FILE_RE = re.compile(r"file=(2022BC_Chapter(\d{2})_[^&\"']+?\.pdf)")
NOTE_RE = re.compile(r"^\s*(‡+)\s*Chapter \d+ was amended by (.+?)\. This law has an effective date of (.+?)\.")
SECTION_RE = re.compile(r"^\s*SECTION BC (\d{3,4})\s*$")
SUB_RE = re.compile(r"^\s*(‡+\s*)?(\d{3,4}(?:\.\d+)+)\s+([A-Z].*?\.)(?:\s+(.*))?$")
TABLE_RE = re.compile(r"^\s*TABLE \d{3,4}")
PARA_START = re.compile(r"^(\d+\.|\d+(?:\.\d+)+\.?|[a-z]\.|\([a-z0-9]+\)|•)\s|^(Exceptions?:|Notes?:)(\s|$)"
                        r"|^[A-Z][A-Z0-9][A-Z0-9 ,/()'’-]*\.(\s|$)")  # a defined TERM. in Chapter 2
MARK_RE = re.compile(r"‡+\s*")


def sort_key(ch: int, sid: str | None) -> str:
    if sid is None:
        return f"{ch:03d}"
    return f"{ch:03d}." + ".".join(f"{int(p):04d}" for p in sid.split("."))


def parse_chapter(ch: int, pages: list[str], pdf_url: str, doc_date: str | None) -> tuple[list[dict], dict]:
    notes: dict[str, tuple[str, str | None]] = {}  # ‡‡‡ -> (Local Law, effective date)
    chapter_title = None
    entries: list[dict] = []
    cur: dict | None = None
    cur_lines: list[str] = []
    cur_table = False
    pending_section: dict | None = None  # SECTION BC xxxx seen, title lines follow

    def close():
        nonlocal cur, cur_lines, cur_table
        if cur is not None:
            cur["body_md"] = reflow(cur_lines, PARA_START)
            entries.append(cur)
        cur, cur_lines, cur_table = None, [], False

    for pno, page in enumerate(pages, 1):
        lines = page.split("\n")
        page_has_text = False
        for i, raw in enumerate(lines):
            line = raw.rstrip()
            s = line.strip()
            if not s:
                if cur is not None:
                    cur_lines.append("")
                continue
            first_on_page, page_has_text = not page_has_text, True
            m = NOTE_RE.match(s)
            if m:
                notes[m.group(1)] = (m.group(2), parse_date(m.group(3)))
                continue
            if chapter_title is None:
                if re.match(rf"^CHAPTER {ch}$", s):
                    # the title is the next non-empty line(s)
                    rest = [x.strip() for x in lines[i + 1:] if x.strip()]
                    chapter_title = nice_title(rest[0]) if rest else f"Chapter {ch}"
                continue
            if (s.upper() == chapter_title.upper() and (pending_section is None or first_on_page)) \
                    or re.fullmatch(r"\d{1,4}", s) or "NEW YORK CITY BUILDING CODE" in s:
                continue  # running header, page number, footer
            m = SECTION_RE.match(s)
            if m:
                close()
                pending_section = {"id": m.group(1), "title_lines": [], "page": pno}
                continue
            if pending_section is not None:
                if s.isupper() and not SUB_RE.match(s) and not TABLE_RE.match(s):
                    pending_section["title_lines"].append(s)
                    continue
                sid = pending_section["id"]
                cur = {"id": sid, "title": nice_title(" ".join(pending_section["title_lines"])) or f"Section {sid}",
                       "level": 2, "parent": f"chapter-{ch}", "url": f"{pdf_url}#page={pending_section['page']}",
                       "last_amended": None, "place": f"Ch {ch}", "sort_key": sort_key(ch, sid)}
                cur_lines = []
                pending_section = None
            m = SUB_RE.match(s)
            if m and int(m.group(2).split(".")[0]) // 100 == ch:
                close()
                marks, sid, title, rest = m.group(1), m.group(2), m.group(3), m.group(4) or ""
                parent = sid.rsplit(".", 1)[0]
                law, date = notes.get((marks or "").strip(), (None, None))
                cur = {"id": sid, "title": title.rstrip("."), "level": 2 + sid.count("."), "parent": parent,
                       "url": f"{pdf_url}#page={pno}", "last_amended": date, "amended_by": law,
                       "place": f"Ch {ch}", "sort_key": sort_key(ch, sid)}
                cur_lines = [rest] if rest else []
                continue
            if cur is not None:
                cur_lines.append(MARK_RE.sub("", line) if "‡" in line else line)
    close()
    seen: set[str] = set()
    for e in entries:
        if e["id"] in seen:
            print(f"    note: {e['id']} appears twice in the chapter {ch} PDF; the first is kept", file=sys.stderr)
        seen.add(e["id"])
    chapter = {"id": f"chapter-{ch}", "title": f"Chapter {ch}: {chapter_title or ''}".strip(), "level": 1, "parent": None,
               "url": pdf_url, "last_amended": None, "place": "", "body_md": "", "sort_key": sort_key(ch, None),
               "notes": [{"local_law": law, "effective": date} for law, date in notes.values()], "document_date": doc_date}
    return [chapter, *entries], chapter


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", type=int, help="one chapter number (for a quick check)")
    ap.add_argument("--force", action="store_true", help="re-download PDFs already cached under raw/bc/")
    a = ap.parse_args()

    f = Fetcher(RAW, delay=0.7, force=a.force)
    index = f.get(INDEX, "index")
    files = {}
    for m in FILE_RE.finditer(index):
        files[int(m.group(2))] = m.group(1)
    print(f"{len(files)} chapter PDFs listed on the DOB page")
    if not a.only and OUT.exists():
        shutil.rmtree(OUT)

    all_entries: list[dict] = []
    latest = ""
    for ch in sorted(files):
        if a.only and ch != a.only:
            continue
        name = files[ch]
        pdf, info = f.download(PDF_BASE + name, name)
        doc_date = http_date(info.get("last_modified"))
        pages = pdf_pages(pdf)
        entries, chapter = parse_chapter(ch, pages, PDF_BASE + name, doc_date)
        latest = max([latest, *(e["last_amended"] or "" for e in entries)])
        n_sec = sum(1 for e in entries if e["level"] == 2)
        print(f"  ch {ch:>2} {chapter['title'][:45]:<46} {n_sec:3} sections {len(entries) - 1 - n_sec:4} subsections  pdf {doc_date}")
        all_entries.extend(entries)
        # one markdown file per section, its subsections inside
        for sec in (e for e in entries if e["level"] == 2):
            subs = [e for e in entries if e["level"] > 2 and (e["id"] + ".").startswith(sec["id"] + ".")]
            body = "\n\n".join(f"{'#' * min(6, e['level'])} {e['id']} {e['title']}"
                               + (f"\n_Amended by {e['amended_by']}, effective {e['last_amended']}_" if e.get("amended_by") else "")
                               + f"\n\n{e['body_md']}" for e in subs)
            write_md(OUT / f"chapter-{ch}" / f"{sec['id']}.md",
                     {"id": sec["id"], "title": sec["title"], "code": "NYC Building Code (2022)", "chapter": ch,
                      "chapter_title": chapter["title"], "url": sec["url"], "pdf_date": doc_date},
                     f"# BC {sec['id']} {sec['title']}\n\n{body}")

    if a.only:
        print(f"--only: {len(all_entries)} entries parsed, nothing written to data/")
        for e in all_entries[:12]:
            print(f"   {e['id']:<14} L{e['level']} parent={str(e['parent']):<12} {e['title'][:40]:<41} {len(e['body_md']):5} chars  {e.get('last_amended') or ''}")
        return
    n = write_jsonl(DATA / "sections.jsonl", all_entries)
    meta = {"source": INDEX, "edition": "2022 NYC Building Code, the 34 chapter PDFs DOB publishes (there is no chapter 33 among them); appendices not included", "fetched_at": f.first_fetch_at or now(),
            "latest_amendment": latest or None, "entries": n, "chapters": len(files),
            "sections": sum(1 for e in all_entries if e["level"] == 2)}
    (DATA / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {n} entries to data/bc/; latest local-law effective date in the text {latest}")


if __name__ == "__main__":
    main()
