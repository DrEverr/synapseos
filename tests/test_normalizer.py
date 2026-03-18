"""Tests for entity name normalization."""

from synapse.resolution.normalizer import normalize_entity_name


def test_lowercase():
    assert normalize_entity_name("Hello World") == "hello world"


def test_whitespace():
    assert normalize_entity_name("  hello   world  ") == "hello world"


def test_trademark_symbols():
    assert normalize_entity_name("SILRES® BS 5137") == "silres bs 5137"
    assert normalize_entity_name("Product™ Name") == "product name"
    assert normalize_entity_name("Brand©") == "brand"


def test_registered_plain_equivalence():
    assert normalize_entity_name("SILRES®") == normalize_entity_name("SILRES")


def test_unicode_normalization():
    # NFKD: ligatures etc.
    assert normalize_entity_name("ﬁne") == "fine"


def test_empty_string():
    assert normalize_entity_name("") == ""


def test_numeric():
    assert normalize_entity_name("500 mPa·s") == "500 mpa·s"


def test_special_chars_preserved():
    result = normalize_entity_name("pH 7.0")
    assert "ph" in result
    assert "7.0" in result
