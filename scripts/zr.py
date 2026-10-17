#!/usr/bin/env python3
"""Command-line access to the Zoning Resolution index (same functions as the MCP server).

  zr search "rear yard corner lot" [--district R6B] [--article II] [--chapter 3] [--scope all] [--limit 10]
  zr get 23-344 [--children] [--parents]
  zr define "corner lot"
  zr chapter II 3
  zr toc [II]
  zr district R6B [--article II]
  zr history 23-344
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "mcp"))
import server as S  # noqa: E402


def main():
    ap = argparse.ArgumentParser(prog="zr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--limit", type=int, default=10)
    p.add_argument("--article"); p.add_argument("--chapter", type=int); p.add_argument("--district")
    p.add_argument("--scope", default="sections", choices=["sections", "definitions", "appendices", "all"])
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("get"); p.add_argument("id"); p.add_argument("--children", action="store_true")
    p.add_argument("--parents", action="store_true"); p.add_argument("--max-chars", type=int, default=60000)

    p = sub.add_parser("define"); p.add_argument("term"); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("chapter"); p.add_argument("article"); p.add_argument("chapter", type=int)
    p = sub.add_parser("toc"); p.add_argument("article", nargs="?")
    p = sub.add_parser("district"); p.add_argument("district"); p.add_argument("--article"); p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("history"); p.add_argument("id")

    a = ap.parse_args()
    if a.cmd == "search":
        out = S.zr_search(a.query, a.limit, a.article, a.chapter, a.district, a.scope)
        if a.json:
            print(out); return
        r = json.loads(out)
        if "error" in r:
            print(r["error"]); return
        print(f"{r['count']} hits for {r['query']!r}" + (f"  ({r['note']})" if r.get("note") else ""))
        for h in r["hits"]:
            if h["type"] == "section":
                print(f"\n{h['id']}  {h['title']}   [Art {h['article']} Ch {h['chapter']} · {h['last_amended']}]\n    {h['url']}\n    {h['snippet']}")
            elif h["type"] == "definition":
                print(f"\n[def] {h['term']}  ({h['applicability']})\n    {h['snippet']}")
            else:
                print(f"\n[appendix] {h['title']}\n    {h['url']}\n    {h['snippet']}")
    elif a.cmd == "get":
        print(S.zr_get_section(a.id, a.children, a.parents, a.max_chars))
    elif a.cmd == "define":
        print(S.zr_define(a.term, a.limit))
    elif a.cmd == "chapter":
        print(S.zr_list_chapter(a.article, a.chapter))
    elif a.cmd == "toc":
        print(S.zr_toc(a.article))
    elif a.cmd == "district":
        print(S.zr_district(a.district, a.article, a.limit))
    elif a.cmd == "history":
        print(S.zr_history(a.id))


if __name__ == "__main__":
    main()
