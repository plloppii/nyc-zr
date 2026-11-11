"""Small helpers shared by the scrapers of the codes other than the Zoning Resolution."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import time

import requests

UA = "nyc-zr/0.3"  # nyc.gov refuses user agents that contain a URL
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


class Fetcher:
    """Polite GET with an on-disk cache: one request at a time, a pause between requests, nothing refetched."""

    def __init__(self, cache_dir: pathlib.Path, delay: float = 0.7, force: bool = False):
        self.cache_dir, self.delay, self.force = cache_dir, delay, force
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.fetched = 0
        self.first_fetch_at: str | None = None

    def get(self, url: str, name: str) -> str:
        path = self.cache_dir / f"{name}.html"
        if path.exists() and not self.force:
            return path.read_text(encoding="utf-8")
        if self.fetched:
            time.sleep(self.delay)
        r = self.s.get(url, timeout=60)
        r.raise_for_status()
        path.write_text(r.text, encoding="utf-8")
        self.fetched += 1
        self.first_fetch_at = self.first_fetch_at or now()
        return r.text


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_date(s: str | None) -> str | None:
    """'Sep. 22, 2014' / 'June 12, 2019' / '9/22/2014' / '2014-09-22' -> '2014-09-22'."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})$", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"
    return None


def norm(s: str) -> str:
    return re.sub(r"[ \t\r\n ­]+", " ", s).strip()


def yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(yaml_scalar(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def write_md(path: pathlib.Path, front: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {yaml_scalar(v)}" for k, v in front.items())
    path.write_text(f"---\n{fm}\n---\n\n{body.rstrip()}\n", encoding="utf-8")


def write_jsonl(path: pathlib.Path, records) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


# ----------------------------------------------------------------------------------------------------------------
# PDFs: the Building Code and the Housing Maintenance Code are published as PDFs with a clean text layer

def download(self: Fetcher, url: str, name: str) -> tuple[pathlib.Path, dict]:
    """Binary download with a sidecar <name>.json holding the response headers that date the file."""
    path = self.cache_dir / name
    side = self.cache_dir / (name + ".json")
    if path.exists() and side.exists() and not self.force:
        return path, json.loads(side.read_text(encoding="utf-8"))
    if self.fetched:
        time.sleep(self.delay)
    r = self.s.get(url, timeout=120)
    r.raise_for_status()
    path.write_bytes(r.content)
    info = {"url": url, "fetched_at": now(), "last_modified": r.headers.get("Last-Modified"),
            "etag": r.headers.get("ETag"), "bytes": len(r.content)}
    side.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    self.fetched += 1
    self.first_fetch_at = self.first_fetch_at or info["fetched_at"]
    return path, info


Fetcher.download = download  # type: ignore[attr-defined]


def http_date(s: str | None) -> str | None:
    """'Thu, 02 Oct 2025 14:03:11 GMT' -> '2025-10-02'."""
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z").date().isoformat()
    except ValueError:
        return None


def pdf_pages(pdf: pathlib.Path) -> list[str]:
    """Text of each page, via poppler's pdftotext -layout (brew install poppler). Cached beside the PDF."""
    import shutil
    import subprocess
    txt = pdf.with_suffix(".txt")
    if not txt.exists() or txt.stat().st_mtime < pdf.stat().st_mtime:
        if not shutil.which("pdftotext"):
            raise SystemExit("pdftotext (poppler) is needed to read the PDFs: brew install poppler")
        subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), str(txt)], check=True)
    return txt.read_text(encoding="utf-8").split("\f")


_SMALL = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
_KEEP_HYPHEN = {"non", "self", "semi", "multi", "anti", "cross", "fire", "one", "two", "three", "four", "half", "pre", "post", "sub"}


def nice_title(s: str) -> str:
    """'FIRE AND SMOKE PROTECTION FEATURES' -> 'Fire and Smoke Protection Features' (acronyms and codes kept)."""
    words = norm(s).split(" ")
    out = []
    for i, w in enumerate(words):
        if re.search(r"\d", w) or (len(w) <= 3 and w.isupper() and i > 0 and w.lower() not in _SMALL):
            out.append(w)
        elif i and w.lower() in _SMALL:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def join_wrapped(a: str, b: str) -> str:
    """Join two lines of one paragraph, rejoining a word the PDF hyphenated at the line end."""
    if a.endswith("-") and b[:1].islower():
        frag = a.rsplit(" ", 1)[-1][:-1].lower()
        return (a if frag in _KEEP_HYPHEN else a[:-1]) + b
    return a + " " + b


def reflow(lines: list[str], starts: re.Pattern, indent_unit: int = 4) -> str:
    """PDF lines -> markdown paragraphs. A line that matches `starts` (a list marker, 'Exceptions:' ...) or follows a
    line ending in ':' begins a paragraph; other lines continue the previous one. Blank lines end a paragraph. Lines
    with wide internal gaps (table rows) are kept verbatim in a fenced block."""
    paras: list[str] = []
    cur: str | None = None
    table: list[str] | None = None

    def flush():
        nonlocal cur, table
        if table:
            paras.append("```\n" + "\n".join(table) + "\n```")
            table = None
        if cur is not None and cur.strip():
            paras.append(cur)
        cur = None

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if re.search(r"\S {3,}\S", line.strip()) or (table is not None and line.startswith(" " * 8)):
            if table is None:
                flush()
                table = []
            table.append(line.strip() if table == [] else line.rstrip())
            continue
        if table is not None:
            flush()
        text = line.strip()
        if cur is None or starts.match(text) or cur.rstrip().endswith(":"):
            if cur is not None:
                flush()
            indent = (len(line) - len(line.lstrip())) // indent_unit
            cur = "  " * min(indent, 4) + text if starts.match(text) else text
        else:
            cur = join_wrapped(cur, text)
    flush()
    return "\n\n".join(paras)
