#!/usr/bin/env python3
"""Command-line access to the Zoning Resolution index (same functions as the MCP server).

  zr search "rear yard corner lot" [--district R6B] [--article II [--chapter 3]] [--scope all] [--limit 10]
  zr get 23-344 [--children] [--parents] [--max-chars N]
  zr define "corner lot"
  zr chapter II 3
  zr toc [II]
  zr district R6B [--article II]
  zr history 23-344            (the only command that touches the network)
  zr info                      (what the text is as of, and when this clone indexed it)

Every command takes --json for machine-readable output. Errors go to stderr with exit status 1 (usage errors: 2).
Plain python3 3.11+ with SQLite FTS5 is enough; the index builds itself from data/*.jsonl on first use.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "mcp"))
import server as S  # noqa: E402

SOURCES = ("sections.jsonl", "definitions.jsonl", "appendices.jsonl", "meta.json")


def ensure_index() -> None:
    """Build data/zr.sqlite from data/*.jsonl when it is missing or older than them (first run after a clone or a pull)."""
    db = ROOT / "data" / "zr.sqlite"
    sources = [ROOT / "data" / f for f in SOURCES]
    if db.exists() and all(not s.exists() or s.stat().st_mtime <= db.stat().st_mtime for s in sources):
        return
    import build_index  # same directory as this script
    print("building data/zr.sqlite from data/*.jsonl (first run, or the data changed) ...", file=sys.stderr)
    r = build_index.build()
    print(f"  {r['sections']} sections, {r['definitions']} definitions, {r['appendices']} appendices", file=sys.stderr)


def info() -> dict:
    con = S.db()
    meta = dict(con.execute("SELECT key, value FROM meta"))
    return {"text_fetched_at": meta.get("fetched_at") or None, "latest_amendment": meta.get("latest_amendment") or None,
            "index_built_at": meta.get("built_at"), "sections": int(meta.get("sections", 0)),
            "definitions": int(meta.get("definitions", 0)), "appendices": int(meta.get("appendices", 0)),
            "source": meta.get("source"), "db": str(S.DB)}


def render_info(d: dict) -> str:
    return "\n".join([
        f"text fetched from {d['source']}: {d['text_fetched_at'] or 'unknown (data/meta.json missing)'}",
        f"latest amendment in the text: {d['latest_amendment'] or 'unknown'}",
        f"local index built: {d['index_built_at']}  ({d['sections']} sections, {d['definitions']} definitions, {d['appendices']} appendices)",
    ])


def emit(result: dict, render, as_json: bool) -> None:
    if "error" in result:
        if as_json:
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        else:
            extras = {k: v for k, v in result.items() if k != "error"}
            print(f"error: {result['error']}" + (f"\n{json.dumps(extras, ensure_ascii=False)}" if extras else ""), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=1) if as_json else render(result))


def main():
    ap = argparse.ArgumentParser(prog="zr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable JSON instead of text")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", parents=[common], help="full-text search (BM25)")
    p.add_argument("query"); p.add_argument("--limit", type=int, default=10)
    p.add_argument("--article", help="roman numeral or number"); p.add_argument("--chapter", type=int, help="requires --article")
    p.add_argument("--district", help="e.g. R6B, C4-4A, M1-5; also matches the parent family")
    p.add_argument("--scope", default="sections", choices=list(S.SCOPES))

    p = sub.add_parser("get", parents=[common], help="one section as markdown")
    p.add_argument("id"); p.add_argument("--children", action="store_true"); p.add_argument("--parents", action="store_true")
    p.add_argument("--max-chars", type=int, default=60000, help="truncate text output (0 = never)")

    p = sub.add_parser("define", parents=[common], help="12-10 definitions of a term"); p.add_argument("term"); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("chapter", parents=[common], help="outline of one chapter"); p.add_argument("article"); p.add_argument("chapter", type=int)
    p = sub.add_parser("toc", parents=[common], help="articles and chapters, or one article"); p.add_argument("article", nargs="?")
    p = sub.add_parser("district", parents=[common], help="sections naming a district"); p.add_argument("district"); p.add_argument("--article"); p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("history", parents=[common], help="live amendment history"); p.add_argument("id")
    sub.add_parser("info", parents=[common], help="dates: text fetched, latest amendment, index built")

    a = ap.parse_args()
    if getattr(a, "article", None):
        _, err = S.resolve_article(a.article)
        if err:
            ap.error(err)
    if a.cmd == "search" and a.chapter and not a.article:
        ap.error("--chapter requires --article (chapter numbers repeat in every article)")

    try:
        ensure_index()
    except (OSError, sqlite3.Error, KeyError, json.JSONDecodeError) as e:
        sys.exit(f"error: could not build data/zr.sqlite: {e}")

    if a.cmd == "search":
        emit(S.search(a.query, a.limit, a.article, a.chapter, a.district, a.scope), S.render_search, a.json)
    elif a.cmd == "get":
        emit(S.get_section(a.id, a.children, a.parents), lambda d: S.render_section(d, a.max_chars), a.json)
    elif a.cmd == "define":
        emit(S.define(a.term, a.limit), S.render_definitions, a.json)
    elif a.cmd == "chapter":
        emit(S.list_chapter(a.article, a.chapter), S.render_chapter, a.json)
    elif a.cmd == "toc":
        emit(S.toc(a.article), S.render_toc, a.json)
    elif a.cmd == "district":
        emit(S.district_sections(a.district, a.article, a.limit), lambda d: json.dumps(d, ensure_ascii=False, indent=1), a.json)
    elif a.cmd == "history":
        emit(S.history(a.id), S.render_history, a.json)
    elif a.cmd == "info":
        emit(info(), render_info, a.json)


if __name__ == "__main__":
    main()
