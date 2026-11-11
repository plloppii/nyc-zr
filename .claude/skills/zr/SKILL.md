---
name: zr
description: Look up NYC building law from the local copy in ~/nyc-zr instead of the web. The NYC Zoning Resolution (FAR, height, yards, lot coverage, use groups, parking, special districts, 12-10 definitions), the 2022 NYC Building Code (egress, light and ventilation, room dimensions, accessibility, fire separation, rooftop structures, fire districts), the NY Multiple Dwelling Law and the NYC Housing Maintenance Code (room sizes, windows, occupancy, cellars). Use for any question about what may be built, altered or laid out in a NYC building.
---

# NYC building law lookup

One command, plain python3, no install. Run `git -C ~/nyc-zr pull` first if the text may have changed.

```bash
python3 ~/nyc-zr/zr.py topic                         # the floorplan topics
python3 ~/nyc-zr/zr.py topic "room size" --text      # the governing sections of every law, with their text
python3 ~/nyc-zr/zr.py search "ceiling height" --code bc        # or no --code to search all four laws
python3 ~/nyc-zr/zr.py search "rear yard corner lot" --district R6B --article II   # zoning filters
python3 ~/nyc-zr/zr.py get 23-344 --parents          # bare id = Zoning Resolution; --children for a group such as 23-30
python3 ~/nyc-zr/zr.py get bc:1208.1                 # Building Code; bc:D101.1 for Appendix D
python3 ~/nyc-zr/zr.py get mdl:31                    # Multiple Dwelling Law
python3 ~/nyc-zr/zr.py get hmc:27-2074               # Housing Maintenance Code
python3 ~/nyc-zr/zr.py define "corner lot"           # ZR 12-10, then BC 202, MDL § 4, HMC § 27-2004
python3 ~/nyc-zr/zr.py toc II  |  toc bc  |  toc bc:chapter-10  |  toc mdl:article-3
python3 ~/nyc-zr/zr.py info                          # what is loaded and what date each law is as of
```

Add `--json` to any command. Errors go to stderr with exit status 1.

How to answer a question about a plan: start with `topic` to find which laws speak to it, read the cited sections,
then search for anything the topic map does not cover. When the query is a defined term, `search` prints the laws'
definitions above the hits; sections with the same title in one law (the MDL repeats a rule for each dwelling
class) collapse into one row that names the others. Quote the citation exactly as printed (`ZR § 23-344`,
`BC 1208.1`, `MDL § 31`, `HMC § 27-2074`) with its `url`. The tool returns the law; deciding whether a plan complies
is the reader's reasoning.

Applicability: before quoting the Multiple Dwelling Law or the Housing Maintenance Code, say whether it applies to
the building, and name any law that does not.
- The MDL covers multiple dwellings only: three or more families living independently (MDL § 4). One- and two-family
  houses are outside it. Within it, provisions turn on the class (class A permanent residence, class B transient)
  and on when the building was erected: old-law and new-law tenements and converted dwellings predate April 18, 1929;
  Article 3 governs multiple dwellings erected after that date. Read the applicability sentence of each section.
- The HMC applies to all dwellings in the city, one- and two-family houses included (HMC § 27-2003), but many
  sections distinguish multiple from private dwellings and class A from class B.
- The Building Code governs new construction and alterations; whether an existing building must meet it or may stay
  under the code it was built to is set by Administrative Code title 28, which is not included here.
- The Zoning Resolution applies by lot and district; a building that predates the current rules may be
  non-complying (`topic "existing buildings"`).

Currency: the text is a snapshot. `info` shows when each law was fetched and its latest recorded amendment, and each
section carries a last-amended date where the source records one. Give that date when it matters. For the Zoning
Resolution, `history <id>` fetches a section's amendment history live.

Scope: the Zoning Resolution governs bulk and use at the lot level; the other three govern what happens inside the
building. Not included: the Plumbing Code (fixture counts), the Mechanical Code, the Fire Code, the Energy
Conservation Code, Administrative Code title 28, Building Code chapter 33 (not on the DOB page), the Building Code
appendices other than D (fire districts), and the zoning maps.
