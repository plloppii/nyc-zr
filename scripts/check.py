#!/usr/bin/env python3
"""Checks that the tool and the text still behave. Run after refreshing a law or changing zr.py:

  python3 scripts/check.py

Standard library only. It builds data/index.sqlite if needed, prints one line per check and exits 1 if any failed.
"""
from __future__ import annotations

import json
import pathlib
import sys
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import zr  # noqa: E402

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


def refs(hits: list[dict]) -> list[str]:
    return [zr.ref(h["code"], h["id"]) for h in hits if h["type"] == "section"]


zr.ensure_index()
con = zr.db()

# every law is loaded
loaded = {r[0] for r in con.execute("SELECT code FROM codes")}
check(loaded == set(zr.CODES), f"all four laws indexed: {', '.join(sorted(loaded))}")

# search ranking: "bulkhead" in either number. BC 1510.2 (Bulkheads and penthouses) leads across all laws; BC 504.3
# (the 33 1/3 percent rooftop-structures exception) stays in the Building Code's own first ten. 504.3 mentions
# bulkheads once in 229 words, so across all laws it ranks below the sections titled "Bulkhead(s)".
for q in ("bulkhead", "bulkheads"):
    top = refs(zr.search(q, 5)["hits"])
    check("bc:1510.2" in top, f"search {q!r}: bc:1510.2 in the top 5 of all laws ({', '.join(top)})")
    bc_top = refs(zr.search(q, 10, "bc")["hits"])
    check("bc:504.3" in bc_top and "bc:1510.2" in bc_top,
          f"search {q!r} --code bc: bc:504.3 and bc:1510.2 in the top 10 (504.3 at {bc_top.index('bc:504.3') + 1 if 'bc:504.3' in bc_top else '-'})")
s = zr.search("bulkhead", 10)
check(any(d["code"] == "bc" and d["text"].startswith("BULKHEAD.") for d in s.get("definitions", [])),
      "search 'bulkhead' prints the BC 202 definition above the hits")
scuttles = [h for h in s["hits"] if h["code"] == "mdl" and h["title"].lower() == "bulkheads and scuttles"]
check(len(scuttles) == 1 and len(scuttles[0].get("also", [])) == 2,
      "the three MDL 'Bulkheads and scuttles' sections collapse into one row listing the other two")
check(any(t["topic"] == "rooftop structures" and "bc:504.3" in t["refs"] for t in s.get("topics", [])),
      "search 'bulkhead' carries the rooftop structures topic and its refs in --json")
check("See topic: rooftop structures" in zr.render_search(s), "search 'bulkhead' prints 'See topic: rooftop structures'")
named = [t["topic"] for t in zr.search("rear yard corner lot", 5).get("topics", [])]
check(named == ["yards and courts"], f"search 'rear yard corner lot' points at {named}")
check(not zr.search("corner lot", 5).get("topics"), "search 'corner lot' names no topic")

# the topic map resolves
topics = json.loads((ROOT / "data/topics.json").read_text(encoding="utf-8"))
for name in (k for k in topics if not k.startswith("_")):
    t = zr.topics(name)
    bad = [f"{c['ref']} ({c['status']})" for c in t["cites"] if c.get("status")]
    check(not bad, f"topic {name!r}: {len(t['cites'])} citations resolve" + (f"; unresolved: {', '.join(bad)}" if bad else ""))

# Zoning Resolution definition links: the DCP site anchors each term as id="term-<term>", so the fragment is encoded
rows = con.execute("SELECT term, url FROM definitions").fetchall()
bad_urls = [r["url"] for r in rows if " " in r["url"] or not r["url"].endswith("#" + quote("term-" + r["term"]))]
check(not bad_urls, f"{len(rows)} definition URLs carry an encoded term anchor" + (f"; first bad: {bad_urls[0]}" if bad_urls else ""))
fa = zr.define("floor area", 1)["definitions"][0]["url"]
check(fa.endswith("12-10#term-floor%20area"), f"floor area links to {fa}")

# one section from each law, Appendix D included, ids case-insensitive for the appendix letter
for ref_, title in (("23-344", None), ("bc:1208.1", None), ("mdl:31", None), ("hmc:27-2074", None),
                    ("bc:D101.1", "Scope"), ("bc:d102.2.4", "Structural fire rating")):
    g = zr.get_section(ref_)
    check("error" not in g and (title is None or g["title"] == title), f"get {ref_}: {g.get('cite', g.get('error'))} {g.get('title', '')}")

# definitions fall through to the other laws, singular or plural
d = zr.define_all("kitchenettes")
check({o["code"] for o in d.get("other_laws", [])} >= {"bc", "hmc"}, "define 'kitchenettes' finds BC 202 and HMC § 27-2004")

print()
if failures:
    print(f"{len(failures)} check(s) failed")
    sys.exit(1)
print("all checks passed")
