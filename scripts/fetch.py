#!/usr/bin/env python3
"""Download every chapter and appendix page of the NYC Zoning Resolution.

Pages are cached under raw/. Re-running only fetches missing files unless
--force is given. Be polite: one request at a time with a short delay.
"""
import argparse, pathlib, sys, time
import requests

BASE = "https://zr.planning.nyc.gov"
ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = "nyc-zr-indexer/0.1 (personal research tool; contact via github)"

def slug(path: str) -> str:
    return path.strip("/").replace("/", "__")

def fetch(session, path, dest, force=False, delay=0.6):
    if dest.exists() and not force and dest.stat().st_size > 10_000:
        return "cached"
    for attempt in range(4):
        try:
            r = session.get(BASE + path, timeout=60)
            if r.status_code == 200 and len(r.text) > 10_000:
                dest.write_text(r.text, encoding="utf-8")
                time.sleep(delay)
                return "fetched"
            print(f"  ! {path}: status {r.status_code}, {len(r.text)} bytes", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  ! {path}: {e}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return "FAILED"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    s = requests.Session(); s.headers["User-Agent"] = UA
    jobs = []
    for line in (ROOT / "data/chapter_urls.txt").read_text().split():
        jobs.append((line, ROOT / "raw/chapters" / f"{slug(line)}.html"))
    for line in (ROOT / "data/appendix_urls.txt").read_text().split():
        jobs.append((line, ROOT / "raw/appendices" / f"{slug(line)}.html"))
    counts = {}
    for i, (path, dest) in enumerate(jobs, 1):
        status = fetch(s, path, dest, args.force)
        counts[status] = counts.get(status, 0) + 1
        print(f"[{i:3}/{len(jobs)}] {status:8} {path}", flush=True)
    print("done:", counts)
    sys.exit(1 if counts.get("FAILED") else 0)

if __name__ == "__main__":
    main()
