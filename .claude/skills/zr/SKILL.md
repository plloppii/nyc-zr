---
name: zr
description: Look up the NYC Zoning Resolution (full text, 4,000+ sections, 12-10 definitions) from the local index in ~/nyc-zr. Use for any question about what NYC zoning permits — FAR, height, yards, lot coverage, use groups, parking, special districts, defined terms — instead of fetching zr.planning.nyc.gov.
---

# NYC Zoning Resolution lookup

Everything runs through one CLI (no network needed except `history`):

```bash
ZR=/Users/noah.pan/nyc-zr; $ZR/.venv/bin/python $ZR/scripts/zr.py <command>
```

| Need | Command |
| --- | --- |
| Find sections | `zr search "rear yard corner lot" --district R6B --article II --limit 8` (words AND-ed; `"phrases"`, `prefix*`, `AND/OR/NOT/NEAR` ok; `--scope all` adds definitions + appendices) |
| Read a section | `zr get 23-344 --parents` · whole group: `zr get 23-30 --children` |
| Defined term (italics in text) | `zr define "corner lot"` (short aliases work: `accessory`) |
| Navigate | `zr toc` · `zr toc II` · `zr chapter II 3` |
| Everything naming a district | `zr district R6B --article II` (also matches the R6 family; `exact` flags the literal token) |
| Amendment history (live) | `zr history 23-344` |

Section ids encode location: first digits = article+chapter (`23-` = Article II Chapter 3), `23-30` is a group,
`23-34` a section in it, `23-344` a subsection. Residence bulk = Art II Ch 3; commercial = Art III; manufacturing =
Art IV; definitions = 12-10; special purpose districts = Art VIII–XIV (Appendix B indexes them).

Always cite the section id and its `url` from the output, and quote the `last_amended` date when it matters.
The index was built from zr.planning.nyc.gov on the date in `data/zr.sqlite` (`meta` table); rebuild with
`fetch.py` → `parse.py` → `build_index.py` if DCP has amended the text since.
