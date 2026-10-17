#!/usr/bin/env python
"""Publish docs/AGENT_GUIDE.md as a Notion page beside the two Zoning Resolution databases, and write a short
pointer to it into each data source's description so an agent that fetches the database sees it.

Env: NOTION_TOKEN or NOTION_API_TOKEN. The parent page is read from the Sections database (same parent).
Re-running updates the existing page in place (page URL stays stable).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from notion_sync import load_state, rich_text, save_state, table_blocks  # noqa: E402

TOKEN = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_TOKEN")
if not TOKEN:
    sys.exit("set NOTION_TOKEN or NOTION_API_TOKEN")
API = "https://api.notion.com/v1"
GUIDE = ROOT / "docs" / "AGENT_GUIDE.md"
TITLE = "README for agents — NYC Zoning Resolution databases"
MIN_INTERVAL = 0.36
_last = 0.0


def req(method: str, path: str, body=None, version="2025-09-03"):
    global _last
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            r = requests.request(method, API + path, json=body, timeout=60,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": version,
                                          "Content-Type": "application/json"})
        except requests.RequestException as e:
            if attempt == 5:
                raise
            print(f"  retry ({e.__class__.__name__})", file=sys.stderr)
            time.sleep(2)
            continue
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "2")))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()
    raise RuntimeError("gave up")


# ----------------------------------------------------------------------------- markdown -> blocks
def rt(s: str) -> list[dict]:
    """rich_text() from the sync script plus `inline code` spans."""
    out: list[dict] = []
    for i, part in enumerate(re.split(r"(`[^`\n]+`)", s)):
        if not part:
            continue
        if i % 2 == 1:
            out.append({"type": "text", "text": {"content": part[1:-1]}, "annotations": {"code": True}})
        else:
            out.extend(x for x in rich_text(part) if x["text"]["content"])
    return out[:100] or [{"type": "text", "text": {"content": ""}}]


def md_to_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush():
        if para:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": rt(" ".join(x.strip() for x in para))}})
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            lang = ln[3:].strip() or "plain text"
            lang = {"json": "json", "bash": "bash", "python": "python", "": "plain text"}.get(lang, "plain text")
            j = i + 1
            code: list[str] = []
            while j < len(lines) and not lines[j].startswith("```"):
                code.append(lines[j])
                j += 1
            text = "\n".join(code)
            blocks.append({"type": "code", "code": {"language": lang, "rich_text": [
                {"type": "text", "text": {"content": text[k:k + 2000]}} for k in range(0, max(1, len(text)), 2000)]}})
            i = j + 1
            continue
        if ln.startswith("|"):
            flush()
            j = i
            tbl: list[str] = []
            while j < len(lines) and lines[j].startswith("|"):
                tbl.append(lines[j])
                j += 1
            blocks.extend(table_blocks("\n".join(tbl)))
            i = j
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", ln)
        if m:
            flush()
            lvl = len(m.group(1))
            blocks.append({"type": f"heading_{lvl}", f"heading_{lvl}": {"rich_text": rt(m.group(2))}})
            i += 1
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", ln)
        if m:
            flush()
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(m.group(1))}})
            i += 1
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", ln)
        if m:
            flush()
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt(m.group(1))}})
            i += 1
            continue
        if ln.startswith("> "):
            flush()
            blocks.append({"type": "quote", "quote": {"rich_text": rt(ln[2:])}})
            i += 1
            continue
        if ln.strip() == "---":
            flush()
            blocks.append({"type": "divider", "divider": {}})
            i += 1
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        para.append(ln)
        i += 1
    flush()
    return blocks


# ----------------------------------------------------------------------------- notion ops
def list_children(block_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        r = req("GET", q)
        out.extend(r["results"])
        if not r.get("has_more"):
            return out
        cursor = r["next_cursor"]


def append(block_id: str, blocks: list[dict]):
    for k in range(0, len(blocks), 100):
        req("PATCH", f"/blocks/{block_id}/children", {"children": blocks[k:k + 100]})


def page_url(page_id: str) -> str:
    return "https://www.notion.so/" + page_id.replace("-", "")


def main():
    state = load_state()
    sections_db = state["databases"]["sections"]
    definitions_db = state["databases"]["definitions"]
    db = req("GET", f"/databases/{sections_db}")
    parent_page = db["parent"]["page_id"]
    sec_ds = db["data_sources"][0]["id"]
    def_ds = req("GET", f"/databases/{definitions_db}")["data_sources"][0]["id"]

    blocks = md_to_blocks(GUIDE.read_text())
    print(f"{len(blocks)} blocks from {GUIDE.relative_to(ROOT)}")

    pages = state.setdefault("pages", {})
    page_id = pages.get("agent_guide")
    if page_id:
        try:
            p = req("GET", f"/pages/{page_id}")
            if p.get("archived") or p.get("in_trash"):
                page_id = None
        except RuntimeError:
            page_id = None
    if page_id:
        old = list_children(page_id)
        print(f"updating existing page: deleting {len(old)} old blocks")
        for b in old:
            req("DELETE", f"/blocks/{b['id']}")
        req("PATCH", f"/pages/{page_id}", {"properties": {"title": {"title": rt(TITLE)}}})
        append(page_id, blocks)
    else:
        p = req("POST", "/pages", {"parent": {"page_id": parent_page}, "icon": {"type": "emoji", "emoji": "🤖"},
                                   "properties": {"title": {"title": rt(TITLE)}}, "children": blocks[:100]})
        page_id = p["id"]
        append(page_id, blocks[100:])
        pages["agent_guide"] = page_id
        save_state(state)
    url = page_url(page_id)
    print("guide page:", url)

    # description on both data sources (fallback to the database endpoint on older API behaviour)
    desc = {
        sec_ds: (f"Read-only mirror of the NYC Zoning Resolution (zr.planning.nyc.gov), one row per section, full text in "
                 f"the page body; snapshot 2026-10-14. Find a section with the filter Section ID equals \"23-344\" (workspace "
                 f"search fuzzy-matches numbers). Subsections: Parent equals \"23-34\". Districts is a multi-select of tokens "
                 f"named in the text. Instructions for agents: {url}"),
        def_ds: (f"Read-only mirror of the NYC Zoning Resolution defined terms (ZR 12-10 and special-district chapters), "
                 f"title = term, body = definition. Filter Term equals \"corner lot\"; prefer Scope = general unless inside "
                 f"the chapter in Applicability. Instructions for agents: {url}"),
    }
    for ds, text in desc.items():
        try:
            req("PATCH", f"/data_sources/{ds}", {"description": [{"type": "text", "text": {"content": text}}]})
            print("description set on data source", ds)
        except RuntimeError as e:
            print("data_sources PATCH failed, falling back to databases endpoint:", str(e)[:120])
            dbid = sections_db if ds == sec_ds else definitions_db
            req("PATCH", f"/databases/{dbid}", {"description": [{"type": "text", "text": {"content": text}}]},
                version="2022-06-28")
            print("description set on database", dbid)


if __name__ == "__main__":
    main()
