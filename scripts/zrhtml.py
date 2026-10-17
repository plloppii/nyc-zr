"""HTML -> Markdown converter tuned to the zr.planning.nyc.gov markup.

Conventions preserved:
  * <strong><em>term</em></strong>  -> **_term_**   (defined terms; also collected)
  * <a class="sec-link-inline">     -> [23-71](https://zr.planning.nyc.gov/.../23-71)
  * nested <ol>                     -> (a) / (1) / (i) / (A) by depth, like the printed ZR
  * <table>                         -> GitHub markdown table, row/colspans expanded
  * <img>                           -> ![alt](absolute url)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://zr.planning.nyc.gov"
SEC_ID_RE = re.compile(r"^\d{1,3}-\d{2,4}$")


def roman_lower(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def alpha(n: int, upper=False) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s.upper() if upper else s


def list_marker(depth: int, n: int) -> str:
    style = depth % 4
    if style == 0:
        return f"({alpha(n)})"
    if style == 1:
        return f"({n})"
    if style == 2:
        return f"({roman_lower(n)})"
    return f"({alpha(n, upper=True)})"


def norm_ws(s: str) -> str:
    return re.sub(r"[ \t\r\n ]+", " ", s)


def abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    # /article-ii/chapter-3#23-71 -> /article-ii/chapter-3/23-71 (canonical section URL)
    href = re.sub(r"^(/article-[ivx]+/chapter-\d+)#(\d{1,3}-\d{2,4})$", r"\1/\2", href)
    return BASE + href


def section_id_from_href(href: str) -> str | None:
    m = re.search(r"#(\d{1,3}-\d{2,4})$", href or "") or re.search(r"/(\d{1,3}-\d{2,4})$", href or "")
    return m.group(1) if m else None


@dataclass
class Collected:
    defined_terms: set = field(default_factory=set)
    cross_refs: set = field(default_factory=set)
    images: list = field(default_factory=list)


class Converter:
    def __init__(self):
        self.c = Collected()

    # ---------- inline ----------
    def inline(self, node) -> str:
        if isinstance(node, NavigableString):
            if isinstance(node, type(node)) and node.__class__.__name__ in ("Comment", "Script", "Stylesheet"):
                return ""
            # literal asterisks (footnote markers like "R3**") must not read as markdown emphasis
            return norm_ws(str(node)).replace("*", "\\*")
        if not isinstance(node, Tag):
            return ""
        name = node.name.lower()
        if name in ("script", "style"):
            return ""
        if name == "br":
            return "\n"
        if name == "img":
            alt = norm_ws(BeautifulSoup(node.get("alt", ""), "lxml").get_text()).strip()
            raw_src = node.get("src", "") or ""
            if raw_src.startswith("data:"):
                # inline base64 figure: no stable URL to link to; keep a visible placeholder
                return f"[figure: {alt or 'inline image'} (embedded in source page)]"
            src = abs_url(raw_src)
            self.c.images.append(src)
            return f"![{alt}]({src})"
        inner = "".join(self.inline(ch) for ch in node.children)
        if name in ("strong", "b"):
            # defined term: <strong><em>x</em></strong> (sometimes with an <a> wrapper)
            em = node.find(["em", "i"])
            if em is not None and norm_ws(em.get_text()).strip() == norm_ws(node.get_text()).strip():
                term = norm_ws(em.get_text()).strip()
                if term:
                    self.c.defined_terms.add(term.lower())
                return self._wrap(inner.replace("*", ""), "**_", "_**")
            if "**" in inner:
                # bold run that itself contains bold-italic defined terms: don't double-wrap
                return inner
            return self._wrap(inner, "**", "**")
        if name in ("em", "i"):
            parent = node.parent
            if parent is not None and parent.name in ("strong", "b"):
                return inner  # handled by strong
            if parent is not None and parent.name == "a" and parent.parent is not None and parent.parent.name in ("strong", "b"):
                return inner
            term = norm_ws(node.get_text()).strip()
            # ZR renders defined terms bold-italic; italic-only text is usually a term too in tables
            return self._wrap(inner, "*", "*")
        if name == "a":
            href = node.get("href", "")
            sid = section_id_from_href(href)
            if "sec-link-inline" in (node.get("class") or []) or sid:
                if sid:
                    self.c.cross_refs.add(sid)
                text = inner.strip() or sid or href
                return f"[{text}]({abs_url(href)})" if href else text
            if href and not href.startswith("#"):
                return f"[{inner.strip()}]({abs_url(href)})"
            return inner
        if name == "sup":
            t = inner.strip()
            return f"^{t}" if t else ""
        if name == "sub":
            t = inner.strip()
            return f"~{t}" if t else ""
        return inner

    @staticmethod
    def _wrap(inner: str, left: str, right: str) -> str:
        s = inner
        lead = len(s) - len(s.lstrip())
        trail = len(s) - len(s.rstrip())
        core = s.strip()
        if not core:
            return s
        return s[:lead] + left + core + right + (s[len(s) - trail:] if trail else "")

    # ---------- block ----------
    def block(self, node, depth=0) -> list[str]:
        """Return list of markdown blocks (strings) for a block-level node."""
        out: list[str] = []
        if isinstance(node, NavigableString):
            t = norm_ws(str(node)).strip()
            if t:
                out.append(t)
            return out
        if not isinstance(node, Tag):
            return out
        name = node.name.lower()
        if name in ("script", "style"):
            return out
        if name in ("ol", "ul"):
            return self.list_block(node, depth)
        if name == "table":
            return [self.table_block(node)]
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = self.inline(node).strip()
            return [f"{'#' * (int(name[1]) + 1)} {t}"] if t else []
        if name in ("p",):
            t = self.inline(node).strip()
            t = re.sub(r" *\n *", "\n", t)
            return [t] if t else []
        if name == "hr":
            return ["---"]
        if name == "blockquote":
            inner = self.blocks(node, depth)
            return ["\n".join("> " + line for b in inner for line in b.splitlines())]
        # div / span / article / section / li wrappers: descend, but keep inline runs together
        return self.blocks(node, depth)

    def blocks(self, node: Tag, depth=0) -> list[str]:
        out: list[str] = []
        run: list[str] = []
        block_names = {"p", "div", "ol", "ul", "table", "h1", "h2", "h3", "h4", "h5", "h6",
                       "hr", "blockquote", "article", "section", "li", "figure", "center"}

        def flush():
            t = norm_ws("".join(run)).strip()
            if t:
                out.append(t)
            run.clear()

        for ch in node.children:
            if isinstance(ch, Tag) and ch.name.lower() in block_names:
                flush()
                out.extend(self.block(ch, depth))
            elif isinstance(ch, Tag) and ch.name.lower() == "br":
                flush()
            else:
                run.append(self.inline(ch))
        flush()
        return out

    def list_block(self, node: Tag, depth: int) -> list[str]:
        ordered = node.name.lower() == "ol"
        start = 1
        m = re.search(r"counter-reset:\s*\w+\s+(\d+)", node.get("style", "") or "")
        if m:
            start = int(m.group(1)) + 1
        if node.get("start"):
            try:
                start = int(node.get("start"))
            except ValueError:
                pass
        items: list[str] = []
        n = start
        indent = "    " * depth
        for li in node.find_all("li", recursive=False):
            marker = list_marker(depth, n) if ordered else "-"
            n += 1
            parts = self.blocks(li, depth + 1)
            if not parts:
                continue
            first, rest = parts[0], parts[1:]
            text = f"{indent}{marker} {first}"
            for r in rest:
                # nested lists already carry their own indentation; other blocks get indented
                if r.startswith("    " * (depth + 1)):
                    text += "\n" + r
                else:
                    text += "\n" + "\n".join(f"{indent}    {line}" for line in r.splitlines())
            items.append(text)
        return ["\n".join(items)] if items else []

    def table_block(self, table: Tag) -> str:
        rows = table.find_all("tr")
        grid: list[list[str | None]] = []
        spans: dict[tuple[int, int], str] = {}  # (row, col) -> text carried by rowspan
        for r, tr in enumerate(rows):
            row: list[str | None] = []
            c = 0
            cells = tr.find_all(["td", "th"], recursive=False)
            ci = 0
            while ci < len(cells) or (r, c) in spans:
                if (r, c) in spans:
                    row.append(spans.pop((r, c)))
                    c += 1
                    continue
                cell = cells[ci]
                ci += 1
                text = " ".join(self.blocks(cell)).replace("\n", " ")
                text = norm_ws(text).strip().replace("|", "\\|")
                try:
                    rs = int(cell.get("rowspan", 1) or 1)
                except ValueError:
                    rs = 1
                try:
                    cs = int(cell.get("colspan", 1) or 1)
                except ValueError:
                    cs = 1
                for k in range(cs):
                    row.append(text if k == 0 else "")
                    for rr in range(1, rs):
                        spans[(r + rr, c + k)] = text if k == 0 else ""
                    c += 1
            grid.append(row)
        if not grid:
            return ""
        width = max(len(r) for r in grid)
        grid = [r + [""] * (width - len(r)) for r in grid]
        # drop fully empty rows
        grid = [r for r in grid if any((x or "").strip() for x in r)]
        if not grid:
            return ""
        lines = ["| " + " | ".join(x or "" for x in grid[0]) + " |",
                 "|" + "|".join(" --- " for _ in range(width)) + "|"]
        for r in grid[1:]:
            lines.append("| " + " | ".join(x or "" for x in r) + " |")
        return "\n".join(lines)


def html_to_markdown(root: Tag) -> tuple[str, Collected]:
    conv = Converter()
    parts = conv.blocks(root)
    md = "\n\n".join(p for p in parts if p.strip())
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md, conv.c


def markdown_to_text(md: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = t.replace("\\*", "\x00")  # protect literal asterisks
    t = t.replace("**_", "").replace("_**", "").replace("**", "").replace("*", "")
    t = t.replace("\x00", "*")
    t = re.sub(r"^\|[-| ]+\|$", "", t, flags=re.M)
    t = t.replace("|", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()
