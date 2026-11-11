---
name: zr
description: Look up NYC building law from the local copy in ~/nyc-zr instead of the web. The NYC Zoning Resolution (FAR, height, yards, lot coverage, use groups, parking, special districts, 12-10 definitions), the 2022 NYC Building Code (egress, light and ventilation, room dimensions, accessibility, fire separation), the NY Multiple Dwelling Law and the NYC Housing Maintenance Code (room sizes, windows, occupancy, cellars). Use for any question about what may be built, altered or laid out in a NYC building.
---

# NYC building law lookup

One command, plain python3, no install. Run `git -C ~/nyc-zr pull` first if the text may have changed.

```bash
python3 ~/nyc-zr/zr.py topic                         # the floorplan topics
python3 ~/nyc-zr/zr.py topic "room size" --text      # the governing sections of every law, with their text
python3 ~/nyc-zr/zr.py search "ceiling height" --code bc        # or no --code to search all four laws
python3 ~/nyc-zr/zr.py search "rear yard corner lot" --district R6B --article II   # zoning filters
python3 ~/nyc-zr/zr.py get 23-344 --parents          # bare id = Zoning Resolution; --children for a group such as 23-30
python3 ~/nyc-zr/zr.py get bc:1208.1                 # Building Code
python3 ~/nyc-zr/zr.py get mdl:31                    # Multiple Dwelling Law
python3 ~/nyc-zr/zr.py get hmc:27-2074               # Housing Maintenance Code
python3 ~/nyc-zr/zr.py define "corner lot"           # Zoning Resolution 12-10 definitions
python3 ~/nyc-zr/zr.py toc II  |  toc bc  |  toc bc:chapter-10  |  toc mdl:article-3
python3 ~/nyc-zr/zr.py info                          # what is loaded and what date each law is as of
```

Add `--json` to any command. Errors go to stderr with exit status 1.

How to answer a question about a plan: start with `topic` to find which laws speak to it, read the cited sections,
then search for anything the topic map does not cover. Quote the citation exactly as printed (`ZR § 23-344`,
`BC 1208.1`, `MDL § 31`, `HMC § 27-2074`) with its `url`, and give the amendment or document date when it matters.
The tool returns the law; deciding whether a plan complies is the reader's reasoning, and the Multiple Dwelling Law
and Housing Maintenance Code often depend on when the building was erected and its class, so read the applicability
paragraph of each section.

Scope: the Zoning Resolution governs bulk and use at the lot level; the other three govern what happens inside the
building. Plumbing fixture counts (Plumbing Code), the Mechanical Code and the Fire Code are not included.
