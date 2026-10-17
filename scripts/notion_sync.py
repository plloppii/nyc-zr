#!/usr/bin/env python3
"""Mirror the Zoning Resolution into Notion.

Creates two databases under a parent page and keeps them in sync with data/*.jsonl:

  "NYC Zoning Resolution — Sections"     one row per section (4,000+), full text as page body
  "NYC Zoning Resolution — Definitions"  one row per definition from 12-10

Environment:
  NOTION_TOKEN            internal integration secret. Share the parent page with the integration first.
  NOTION_PARENT_PAGE_ID   page id (32 hex chars, dashes optional) under which to create the databases.

State lives in data/notion_state.json (database ids, page ids, content hashes) so re-runs only push
sections whose text or amendment date changed. Changed sections are archived and re-created, which is
far cheaper than deleting blocks one by one.

Usage:
  .venv/bin/python scripts/notion_sync.py --sections --definitions            # everything
  .venv/bin/python scripts/notion_sync.py --sections --article II --chapter 3  # one chapter
  .venv/bin/python scripts/notion_sync.py --sections --limit 20 --dry-run      # inspect block counts only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE_PATH = DATA / "notion_state.json"
API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
RICH_LIMIT = 2000          # chars per rich_text item
BLOCK_BATCH = 100          # blocks per create/append request
TABLE_ROWS_PER_BLOCK = 99  # rows per table block (header + 99 keeps us under the 100-children limit)
MIN_INTERVAL = 0.36        # seconds between requests (Notion allows ~3 rps)

ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV"]


# ----------------------------------------------------------------------------- Notion client
class Notion:
    def __init__(self, token: str, dry_run=False):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
                               "Content-Type": "application/json"})
        self.last = 0.0
        self.dry_run = dry_run
        self.calls = 0

    def req(self, method: str, path: str, **kw):
        if self.dry_run:
            self.calls += 1
            return {"id": f"dry-{self.calls}", "results": []}
        for attempt in range(6):
            wait = MIN_INTERVAL - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            r = self.s.request(method, API + path, timeout=60, **kw)
            self.last = time.time()
            self.calls += 1
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "2")) + 0.5)
                continue
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
            return r.json()
        raise RuntimeError(f"{method} {path}: gave up after retries")


# ----------------------------------------------------------------------------- markdown -> Notion blocks
INLINE_RE = re.compile(
    r"\*\*_(.+?)_\*\*"                      # bold italic (defined term)
    r"|\*\*(.+?)\*\*"                       # bold
    r"|(?<!\\)\*(.+?)(?<!\\)\*"             # italic *x*
    r"|\[([^\]]+)\]\(([^)\s]+)\)"           # link
    r"|(?<![\w*\\])_([^_\n]+?)_(?![\w*])"   # italic _x_
)


def rich_text(md: str) -> list[dict]:
    """Inline markdown -> Notion rich_text array (bold/italic/links, nesting allowed), chunked to 2000 chars."""
    items: list[dict] = []

    def push(text: str, bold: bool, italic: bool, link: str | None):
        text = text.replace("\\*", "*")
        for i in range(0, len(text), RICH_LIMIT):
            chunk = text[i:i + RICH_LIMIT]
            if not chunk:
                continue
            it = {"type": "text", "text": {"content": chunk}}
            if link:
                it["text"]["link"] = {"url": link}
            ann = {}
            if bold:
                ann["bold"] = True
            if italic:
                ann["italic"] = True
            if ann:
                it["annotations"] = ann
            items.append(it)

    def parse(s: str, bold=False, italic=False, link=None, depth=0):
        pos = 0
        for m in (INLINE_RE.finditer(s) if depth < 4 else []):
            if m.start() > pos:
                push(s[pos:m.start()], bold, italic, link)
            if m.group(1) is not None:
                parse(m.group(1), True, True, link, depth + 1)
            elif m.group(2) is not None:
                parse(m.group(2), True, italic, link, depth + 1)
            elif m.group(3) is not None:
                parse(m.group(3), bold, True, link, depth + 1)
            elif m.group(4) is not None:
                parse(m.group(4), bold, italic, m.group(5), depth + 1)
            else:
                parse(m.group(6), bold, True, link, depth + 1)
            pos = m.end()
        if pos < len(s):
            push(s[pos:], bold, italic, link)

    parse(md)
    return items[:100] or [{"type": "text", "text": {"content": ""}}]


def table_blocks(md: str) -> list[dict]:
    lines = [ln for ln in md.splitlines() if ln.strip()]
    rows = []
    for ln in lines:
        if re.match(r"^\|[\s\-|]+\|$", ln):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split(" | ")]
        rows.append([c.replace("\\|", "|") for c in cells])
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    blocks = []
    for i in range(0, max(1, len(body)), TABLE_ROWS_PER_BLOCK):
        chunk = body[i:i + TABLE_ROWS_PER_BLOCK]
        children = [{"type": "table_row", "table_row": {"cells": [rich_text(c) for c in header]}}]
        children += [{"type": "table_row", "table_row": {"cells": [rich_text(c) for c in r]}} for r in chunk]
        blocks.append({"type": "table", "table": {"table_width": width, "has_column_header": True,
                                                   "has_row_header": False, "children": children}})
    return blocks


def image_block(url: str, alt: str = "") -> dict:
    """External image block, or a paragraph fallback when Notion would reject the URL (non-http, too long)."""
    if url.startswith("http") and len(url) <= 2000:
        b = {"type": "image", "image": {"type": "external", "external": {"url": url}}}
        if alt:
            b["image"]["caption"] = rich_text(alt)
        return b
    return {"type": "paragraph", "paragraph": {"rich_text": rich_text(f"[figure: {alt or 'image'}]")}}


def md_to_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    for para in re.split(r"\n\s*\n", md.strip()):
        p = para.strip("\n")
        if not p.strip():
            continue
        if p.lstrip().startswith("|") and "---" in p:
            blocks.extend(table_blocks(p))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", p)
        if m and "\n" not in p:
            level = min(3, max(1, len(m.group(1)) - 1))
            blocks.append({"type": f"heading_{level}", f"heading_{level}": {"rich_text": rich_text(m.group(2))}})
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", p.strip())
        if m:
            blocks.append(image_block(m.group(2), m.group(1)))
            continue
        # list-style blocks keep one paragraph per line so indentation/markers survive
        lines = p.split("\n")
        if len(lines) > 1 and re.match(r"^\s*\([a-z0-9]+\)\s", lines[0]):
            for ln in lines:
                mi = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", ln)
                if mi:
                    blocks.append(image_block(mi.group(2), mi.group(1)))
                    continue
                blocks.append({"type": "paragraph", "paragraph": {"rich_text": rich_text(ln.rstrip())}})
            continue
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": rich_text(p)}})
    return blocks


# ----------------------------------------------------------------------------- properties
def prop_title(s): return {"title": [{"type": "text", "text": {"content": s[:RICH_LIMIT]}}]}
def prop_text(s): return {"rich_text": [{"type": "text", "text": {"content": (s or "")[:RICH_LIMIT]}}] if s else []}
def _opt(s): return s.replace(",", ";")[:100]  # Notion forbids commas in select option names
def prop_select(s): return {"select": {"name": _opt(s)}} if s else {"select": None}
def prop_multi(xs): return {"multi_select": [{"name": _opt(x)} for x in xs[:100]]}
def prop_date(s): return {"date": {"start": s}} if s else {"date": None}
def prop_url(s): return {"url": s or None}
def prop_num(n): return {"number": n}


SECTION_SCHEMA = {
    "Name": {"title": {}},
    "Section ID": {"rich_text": {}},
    "Article": {"select": {}},
    "Chapter": {"select": {}},
    "Level": {"number": {"format": "number"}},
    "Parent": {"rich_text": {}},
    "Last Amended": {"date": {}},
    "Districts": {"multi_select": {}},
    "Defined Terms": {"rich_text": {}},
    "Cross Refs": {"rich_text": {}},
    "URL": {"url": {}},
    "Node ID": {"number": {"format": "number"}},
    "Hash": {"rich_text": {}},
}
DEFINITION_SCHEMA = {
    "Term": {"title": {}},
    "Key": {"rich_text": {}},
    "Scope": {"select": {}},
    "Applicability": {"rich_text": {}},
    "Last Amended": {"date": {}},
    "Source Section": {"rich_text": {}},
    "URL": {"url": {}},
    "Hash": {"rich_text": {}},
}


def section_props(r: dict, h: str) -> dict:
    return {
        "Name": prop_title(f"{r['id']} {r['title']}".strip()),
        "Section ID": prop_text(r["id"]),
        "Article": prop_select(f"{r['article']} — {r['article_title']}"),
        "Chapter": prop_select(f"{r['article']}-{r['chapter']} {r['chapter_title']}"),
        "Level": prop_num(r["level"]),
        "Parent": prop_text(r["parent"] or ""),
        "Last Amended": prop_date(r["last_amended"]),
        "Districts": prop_multi(r["districts"]),
        "Defined Terms": prop_text(", ".join(r["defined_terms"])),
        "Cross Refs": prop_text(", ".join(r["cross_refs"])),
        "URL": prop_url(r["url"]),
        "Node ID": prop_num(r["node_id"]),
        "Hash": prop_text(h),
    }


def definition_props(d: dict, h: str) -> dict:
    return {
        "Term": prop_title(d["term"]),
        "Key": prop_text(f"{d['term']}|{d['applicability']}"),
        "Scope": prop_select(d["scope"]),
        "Applicability": prop_text(d["applicability"]),
        "Last Amended": prop_date(d["last_amended"]),
        "Source Section": prop_text(d["source_section"]),
        "URL": prop_url(d["url"]),
        "Hash": prop_text(h),
    }


# ----------------------------------------------------------------------------- sync
def content_hash(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:16]


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"databases": {}, "sections": {}, "definitions": {}}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def ensure_database(n: Notion, state: dict, key: str, title: str, schema: dict, parent_page: str) -> str:
    if key in state["databases"]:
        return state["databases"][key]
    if not parent_page:
        sys.exit("NOTION_PARENT_PAGE_ID is required to create the databases on first run")
    res = n.req("POST", "/databases", json={
        "parent": {"type": "page_id", "page_id": parent_page},
        "title": [{"type": "text", "text": {"content": title}}],
        "is_inline": False,
        "properties": schema,
    })
    state["databases"][key] = res["id"]
    save_state(state)
    print(f"created database {title!r}: {res['id']}")
    return res["id"]


def create_page(n: Notion, database_id: str, props: dict, blocks: list[dict]) -> str:
    first, rest = blocks[:BLOCK_BATCH], blocks[BLOCK_BATCH:]
    page = n.req("POST", "/pages", json={"parent": {"database_id": database_id}, "properties": props, "children": first})
    for i in range(0, len(rest), BLOCK_BATCH):
        n.req("PATCH", f"/blocks/{page['id']}/children", json={"children": rest[i:i + BLOCK_BATCH]})
    return page["id"]


def archive_page(n: Notion, page_id: str):
    n.req("PATCH", f"/pages/{page_id}", json={"archived": True})


def load_existing(n: Notion, database_id: str, key_prop: str) -> dict[str, dict]:
    """All live pages in the database keyed by their key property (100 per call), so a crash between
    create and state-save never duplicates a page and re-runs don't need one query per record."""
    out: dict[str, dict] = {}
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = n.req("POST", f"/databases/{database_id}/query", json=body)
        for p in res.get("results", []):
            if p.get("archived"):
                continue
            props = p["properties"]
            key = "".join(t["plain_text"] for t in props.get(key_prop, {}).get("rich_text", []))
            h = "".join(t["plain_text"] for t in props.get("Hash", {}).get("rich_text", []))
            if key and key not in out:
                out[key] = {"page_id": p["id"], "hash": h}
        if not res.get("has_more"):
            return out
        cursor = res.get("next_cursor")


def sync_records(n: Notion, state: dict, kind: str, database_id: str, records, key_fn, props_fn, body_fn, hash_fn, dry_run,
                 key_prop: str = "Section ID", force: bool = False):
    done = skipped = adopted = 0
    existing = {} if dry_run else load_existing(n, database_id, key_prop)
    for key, info in existing.items():
        if key not in state[kind]:
            state[kind][key] = info
            adopted += 1
    if adopted:
        save_state(state)
    for i, r in enumerate(records, 1):
        key = key_fn(r)
        h = hash_fn(r)
        prev = state[kind].get(key)
        if prev and prev.get("hash") == h and not force:
            skipped += 1
            continue
        blocks = md_to_blocks(body_fn(r))
        if dry_run:
            print(f"  would push {key}: {len(blocks)} blocks")
            done += 1
            continue
        if prev and prev.get("page_id"):
            try:
                archive_page(n, prev["page_id"])
            except RuntimeError as e:
                print(f"  (archive failed for {key}: {e})")
        page_id = create_page(n, database_id, props_fn(r, h), blocks)
        state[kind][key] = {"page_id": page_id, "hash": h}
        done += 1
        save_state(state)
        if done % 25 == 0:
            print(f"  {kind}: {done} pushed, {skipped} unchanged, {adopted} adopted ({n.calls} API calls)", flush=True)
    save_state(state)
    print(f"{kind}: {done} pushed, {skipped} unchanged, {adopted} adopted, {n.calls} API calls total", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sections", action="store_true")
    ap.add_argument("--definitions", action="store_true")
    ap.add_argument("--article", help="roman numeral, e.g. II")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-push even when the content hash is unchanged (archive + recreate)")
    args = ap.parse_args()
    if not (args.sections or args.definitions):
        ap.error("pass --sections and/or --definitions")

    token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_TOKEN")
    if not token and not args.dry_run:
        sys.exit("set NOTION_TOKEN or NOTION_API_TOKEN (internal integration secret)")
    parent = (os.environ.get("NOTION_PARENT_PAGE_ID") or "").replace("-", "")
    n = Notion(token or "dry", dry_run=args.dry_run)
    state = load_state()

    if args.sections:
        recs = [json.loads(l) for l in (DATA / "sections.jsonl").open(encoding="utf-8")]
        seen = set()
        recs = [r for r in recs if not (r["id"] in seen or seen.add(r["id"]))]
        if args.article:
            recs = [r for r in recs if r["article"].upper() == args.article.upper()]
        if args.chapter:
            recs = [r for r in recs if r["chapter"] == args.chapter]
        if args.limit:
            recs = recs[:args.limit]
        db_id = "dry" if args.dry_run else ensure_database(n, state, "sections", "NYC Zoning Resolution — Sections", SECTION_SCHEMA, parent)
        print(f"syncing {len(recs)} sections")
        sync_records(n, state, "sections", db_id, recs,
                     key_fn=lambda r: r["id"],
                     props_fn=section_props,
                     body_fn=lambda r: f"*Last amended {r['last_amended'] or 'n/a'} · [source]({r['url']})*\n\n{r['body_md']}",
                     hash_fn=lambda r: content_hash(r["title"], r["body_md"], r["last_amended"] or ""),
                     dry_run=args.dry_run, force=args.force)

    if args.definitions:
        defs = [json.loads(l) for l in (DATA / "definitions.jsonl").open(encoding="utf-8")]
        if args.limit:
            defs = defs[:args.limit]
        db_id = "dry" if args.dry_run else ensure_database(n, state, "definitions", "NYC Zoning Resolution — Definitions", DEFINITION_SCHEMA, parent)
        print(f"syncing {len(defs)} definitions")
        sync_records(n, state, "definitions", db_id, defs,
                     key_fn=lambda d: f"{d['term']}|{d['applicability']}",
                     props_fn=definition_props,
                     body_fn=lambda d: f"*{d['applicability']} · source {d['source_section']} · [12-10]({d['url']})*\n\n{d['definition_md']}",
                     hash_fn=lambda d: content_hash(d["term"], d["definition_md"], d["last_amended"] or ""),
                     dry_run=args.dry_run, key_prop="Key", force=args.force)


if __name__ == "__main__":
    main()
