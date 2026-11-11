#!/usr/bin/env python3
"""Download every chapter and appendix page of the NYC Zoning Resolution.

Pages are cached under raw/zr/. Re-running only fetches missing files unless --force is given. Before fetching, the
site's home and article pages are read to discover chapters and appendices that data/zr/chapter_urls.txt and
data/zr/appendix_urls.txt do not list yet; new ones are appended to those lists (--no-discover skips this). Be polite:
one request at a time with a short delay.
"""
import argparse
import pathlib
import re
import sys
import time

import requests

BASE = "https://zr.planning.nyc.gov"
ROOT = pathlib.Path(__file__).resolve().parent.parent
CH_LIST = ROOT / "data/zr/chapter_urls.txt"
AP_LIST = ROOT / "data/zr/appendix_urls.txt"
UA = "nyc-zr-indexer/0.2 (personal research tool; https://github.com/plloppii/nyc-zr)"

ARTICLE_HREF = re.compile(r'href="(/article-[ivx]+)"')
CHAPTER_HREF = re.compile(r'href="(/article-[ivx]+/chapter-\d+)"')
APPENDIX_HREF = re.compile(r'href="(/appendix-[a-z0-9-]+)"')


def roman_to_int(s: str) -> int:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50}
    total = 0
    for i, ch in enumerate(s.lower()):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1].lower()] > v else v
    return total


def chapter_key(path: str):
    m = re.match(r"/article-([ivx]+)/chapter-(\d+)", path)
    return (roman_to_int(m.group(1)), int(m.group(2))) if m else (99, 0)


def slug(path: str) -> str:
    return path.strip("/").replace("/", "__")


def fetch(session, path, dest, force=False, delay=0.6):
    if dest.exists() and not force and dest.stat().st_size > 10_000:
        return "cached"
    for attempt in range(4):
        try:
            r = session.get(BASE + path, timeout=60)
            if r.status_code == 200 and len(r.text) > 10_000:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(r.text, encoding="utf-8")
                time.sleep(delay)
                return "fetched"
            print(f"  ! {path}: status {r.status_code}, {len(r.text)} bytes", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  ! {path}: {e}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return "FAILED"


def get_html(session, path, delay=0.6) -> str:
    r = session.get(BASE + path, timeout=60)
    r.raise_for_status()
    time.sleep(delay)
    return r.text


def discover(session) -> tuple[set[str], set[str]]:
    """Chapter and appendix paths the site lists now: the home page names the articles, each article page its
    chapters, and every page the appendices."""
    home = get_html(session, "/")
    articles = sorted(set(ARTICLE_HREF.findall(home)), key=lambda p: roman_to_int(p.split("-")[1]))
    if not articles:  # the home page changed shape; the chapter pages carry the same navigation
        home = get_html(session, "/article-i/chapter-1")
        articles = sorted(set(ARTICLE_HREF.findall(home)), key=lambda p: roman_to_int(p.split("-")[1]))
    chapters: set[str] = set()
    appendices = set(APPENDIX_HREF.findall(home))
    for art in articles:
        page = get_html(session, art)
        chapters.update(h for h in CHAPTER_HREF.findall(page) if h.startswith(art + "/"))
        appendices.update(APPENDIX_HREF.findall(page))
    return chapters, appendices


def read_list(p: pathlib.Path) -> list[str]:
    return [line for line in p.read_text().split() if line]


def reconcile(name: str, have: list[str], found: set[str], key) -> list[str]:
    """Merge discovered paths into the committed list; report what is new or no longer linked."""
    new = sorted(found - set(have), key=key)
    gone = sorted(set(have) - found, key=key)
    if new:
        print(f"  + {len(new)} {name} page(s) on the site but not in data/{name}_urls.txt, adding: {' '.join(new)}")
    if gone:
        print(f"  ? {len(gone)} {name} page(s) in data/{name}_urls.txt no longer linked from the site: {' '.join(gone)}\n"
              f"    kept; remove them from the list and raw/ by hand if DCP retired them", file=sys.stderr)
    return sorted(set(have) | found, key=key) if new else have


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="refetch pages already cached under raw/")
    ap.add_argument("--no-discover", action="store_true", help="trust data/*_urls.txt; do not look for new chapters/appendices")
    args = ap.parse_args()
    s = requests.Session()
    s.headers["User-Agent"] = UA

    chapters, appendices = read_list(CH_LIST), read_list(AP_LIST)
    if not args.no_discover:
        try:
            found_ch, found_ap = discover(s)
        except requests.RequestException as e:
            print(f"  ! discovery failed ({e}); using the committed url lists", file=sys.stderr)
        else:
            merged_ch = reconcile("chapter", chapters, found_ch, chapter_key)
            merged_ap = reconcile("appendix", appendices, found_ap, str)
            if merged_ch != chapters:
                CH_LIST.write_text("\n".join(merged_ch) + "\n")
            if merged_ap != appendices:
                AP_LIST.write_text("\n".join(merged_ap) + "\n")
            chapters, appendices = merged_ch, merged_ap

    jobs = [(p, ROOT / "raw/zr/chapters" / f"{slug(p)}.html") for p in chapters]
    jobs += [(p, ROOT / "raw/zr/appendices" / f"{slug(p)}.html") for p in appendices]
    counts: dict[str, int] = {}
    for i, (path, dest) in enumerate(jobs, 1):
        status = fetch(s, path, dest, args.force)
        counts[status] = counts.get(status, 0) + 1
        print(f"[{i:3}/{len(jobs)}] {status:8} {path}", flush=True)
    print("done:", counts)
    sys.exit(1 if counts.get("FAILED") else 0)


if __name__ == "__main__":
    main()
