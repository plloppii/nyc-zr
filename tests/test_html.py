"""HTML -> markdown conversion against snippets of the site's markup (needs bs4/lxml from `uv sync`)."""
import pytest

bs4 = pytest.importorskip("bs4")
from zrhtml import html_to_markdown  # noqa: E402


def conv(html):
    return html_to_markdown(bs4.BeautifulSoup(html, "lxml").body)


def test_defined_terms_and_cross_refs():
    md, c = conv('<p>In all <strong><em>rear yards</em></strong> see Section '
                 '<a class="sec-link-inline" href="/article-ii/chapter-3#23-342">23-342</a>.</p>')
    assert md == "In all **_rear yards_** see Section [23-342](https://zr.planning.nyc.gov/article-ii/chapter-3/23-342)."
    assert c.defined_terms == {"rear yards"} and c.cross_refs == {"23-342"}


def test_nested_list_markers_follow_the_printed_resolution():
    md, _ = conv("<ol><li>first<ol><li>inner<ol><li>deep</li></ol></li></ol></li><li>second</li></ol>")
    assert md.splitlines() == ["(a) first", "    (1) inner", "        (i) deep", "(b) second"]


def test_table_expands_spans_and_escapes_pipes():
    md, _ = conv('<table><tr><th>District</th><th colspan="2">FAR</th></tr>'
                 '<tr><td rowspan="2">R6</td><td>a|b</td><td>2.0</td></tr><tr><td>c</td><td>3.0</td></tr></table>')
    assert md.splitlines() == ["| District | FAR |  |", "| --- | --- | --- |", "| R6 | a\\|b | 2.0 |", "| R6 | c | 3.0 |"]


def test_literal_asterisks_and_images():
    md, c = conv('<p>R3** footnote <img src="/sites/default/files/f.jpg" alt="Fig"></p>')
    assert md == "R3\\*\\* footnote ![Fig](https://zr.planning.nyc.gov/sites/default/files/f.jpg)"
    assert c.images == ["https://zr.planning.nyc.gov/sites/default/files/f.jpg"]
