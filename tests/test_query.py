"""Query parsing in mcp/server.py (standard library only)."""
import pytest
import server as S


def test_fts_query_quotes_plain_words_and_keeps_prefix():
    assert S.fts_query("rear yard") == '"rear" "yard"'
    assert S.fts_query("park*") == '"park"*'
    assert S.fts_query("") == '""'


def test_fts_query_passes_operators_and_phrases_through():
    assert S.fts_query('"rear yard" corner') == '"rear yard" corner'
    assert S.fts_query("yard NOT court") == "yard NOT court"


def test_split_terms_keeps_phrases_whole():
    assert S.split_terms('"rear yard equivalent" zzz') == ['"rear yard equivalent"', "zzz"]
    assert S.split_terms('"rear" "yard"*') == ['"rear"', '"yard"*']


def test_can_relax_only_for_plain_word_and_phrase_queries():
    assert S.can_relax('"rear" "yard"')
    assert S.can_relax('"rear yard" corner')
    assert not S.can_relax('"rear"')                  # one term: nothing to OR
    assert not S.can_relax("yard NOT court")          # operators already express intent
    assert not S.can_relax("NEAR(yard court)")


def test_district_family():
    assert S.district_family("R6B") == ["R6B", "R6"]
    assert S.district_family("C4-4A") == ["C4-4A", "C4-4", "C4"]
    assert S.district_family("m1-5") == ["M1-5", "M1"]
    assert S.district_family("R6") == ["R6"]


@pytest.mark.parametrize("given,expected", [("II", 2), ("ii", 2), ("2", 2), (14, 14), ("XIV", 14), (None, None), ("", None)])
def test_article_num(given, expected):
    assert S.article_num(given) == expected


def test_resolve_article_rejects_unknown():
    assert S.resolve_article("II") == (2, None)
    assert S.resolve_article(None) == (None, None)
    for bad in ("XV", "0", "15", "2I", "Article II"):
        n, err = S.resolve_article(bad)
        assert n is None and err and "unknown article" in err, bad
