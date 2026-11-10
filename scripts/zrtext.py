"""Text helpers shared by the HTML converter, the parser and the index builder. Standard library only, so
scripts/build_index.py (and therefore the no-install CLI) can derive plain text from the committed markdown."""
from __future__ import annotations

import re

# Zoning district tokens, e.g. R6B, R7-2, C4-4A, C1-3 overlays, M1-5/R7X (both halves), M1-1, M1-6D
DISTRICT_RE = re.compile(r"\b(R(?:1[0-2]|[1-9])(?:[A-Z]|-\d[A-Z]?)?|C[1-8](?:-\d{1,2}[A-Z]?)?|M[1-3](?:-\d[A-Z]?)?)\b")

# "R6 through R12", "C1 thru C8", "R3 to R9", "R1 - R5", "M1-2 through M1-5": a range names every district between
# its endpoints, which the token regex alone cannot see. "and" is an enumeration, not a range, so it is left alone.
_TOKEN = r"[RCM]\d{1,2}(?:[A-Z]|-\d{1,2}[A-Z]?)?"
RANGE_RE = re.compile(rf"\b({_TOKEN})\s+(?:(?i:through|thru|to)|[-–])\s+({_TOKEN})\b")
_PARTS_RE = re.compile(r"^([RCM])(\d{1,2})(?:([A-Z])|-(\d{1,2})([A-Z])?)?$")
_MAX_BASE = {"R": 12, "C": 8, "M": 3}


def norm_ws(s: str) -> str:
    return re.sub(r"[ \t\r\n\u00a0]+", " ", s)  # includes the non-breaking space the site uses


def _parts(token: str):
    m = _PARTS_RE.match(token)
    if not m:
        return None
    letter, num, suffix, dash, dash_suffix = m.groups()
    num = int(num)
    if not 1 <= num <= _MAX_BASE[letter]:
        return None
    return letter, num, suffix, (int(dash) if dash else None), dash_suffix


def expand_district_range(lo: str, hi: str) -> set[str]:
    """Districts named by the range lo..hi, endpoints included. Empty when the pair is not an expandable range.

    R6..R12 -> R6 R7 ... R12; M1-2..M1-5 -> M1-2 M1-3 M1-4 M1-5; R3-2..R12 -> R3-2 R4 R5 ... R12 (the lower end is
    a specific district, so its own base R3 is not implied). Letter-suffix pairs such as R7A..R7D are left alone.
    """
    a, b = _parts(lo), _parts(hi)
    if not a or not b or a[0] != b[0]:
        return set()
    letter, lo_num, lo_suffix, lo_dash, lo_dash_suffix = a
    _, hi_num, hi_suffix, hi_dash, hi_dash_suffix = b
    out = {lo, hi}
    hi_is_base = hi_suffix is None and hi_dash is None
    if lo_dash is not None and hi_dash is not None and lo_num == hi_num and lo_dash_suffix == hi_dash_suffix and lo_dash < hi_dash:
        out.update(f"{letter}{lo_num}-{k}{lo_dash_suffix or ''}" for k in range(lo_dash, hi_dash + 1))
    elif hi_is_base and lo_num < hi_num:
        first = lo_num if (lo_suffix is None and lo_dash is None) else lo_num + 1
        out.update(f"{letter}{n}" for n in range(first, hi_num + 1))
    return out


def extract_districts(text: str) -> list[str]:
    """Every district token in the text, with 'X through Y' ranges expanded, sorted."""
    found = set(DISTRICT_RE.findall(text))
    for lo, hi in RANGE_RE.findall(text):
        found.update(expand_district_range(lo, hi))
    return sorted(found)


def markdown_to_text(md: str) -> str:
    """Strip the markdown produced by zrhtml down to searchable plain text."""
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
