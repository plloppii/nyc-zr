"""Pure-text helpers: district tokens and ranges, markdown -> text. Standard library only."""
from zrtext import expand_district_range, extract_districts, markdown_to_text


def test_tokens_without_ranges():
    assert extract_districts("In R6B and C4-4A Districts, M1-5/R7X") == ["C4-4A", "M1-5", "R6B", "R7X"]


def test_base_range_expands_every_district_between():
    assert expand_district_range("R6", "R12") == {"R6", "R7", "R8", "R9", "R10", "R11", "R12"}
    assert extract_districts("In R1 through R5 Districts") == ["R1", "R2", "R3", "R4", "R5"]


def test_range_connectors():
    for text in ("C1 thru C3", "C1 to C3", "C1 - C3", "C1 – C3", "C1 through C3"):
        assert extract_districts(text) == ["C1", "C2", "C3"], text


def test_dashed_range_same_base():
    assert extract_districts("M1-2 through M1-5") == ["M1-2", "M1-3", "M1-4", "M1-5"]
    assert extract_districts("M1-1D through M1-5D") == ["M1-1D", "M1-2D", "M1-3D", "M1-4D", "M1-5D"]


def test_specific_lower_end_does_not_imply_its_base():
    assert extract_districts("R3-2 through R12") == ["R10", "R11", "R12", "R3-2", "R4", "R5", "R6", "R7", "R8", "R9"]


def test_non_ranges_left_alone():
    assert extract_districts("R4A and R5A") == ["R4A", "R5A"]        # enumeration, not a range
    assert expand_district_range("R7A", "R7D") == {"R7A", "R7D"}   # letter suffixes are not sequential
    assert expand_district_range("R6", "C4") == set()              # different series
    assert "R7-2" in extract_districts("R7-2 Districts") and "R7" not in extract_districts("R7-2 Districts")


def test_markdown_to_text():
    md = "A **_rear yard_** per Section [23-342](https://x/23-342) with R3\\* ![fig](https://x/f.jpg)\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    t = markdown_to_text(md)
    assert "rear yard" in t and "**" not in t and "https://x/23-342" not in t and "23-342" in t
    assert "R3*" in t and "![" not in t and "---" not in t and "|" not in t
