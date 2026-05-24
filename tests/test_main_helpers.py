"""Unit tests for _safe_header() and _extract_image() in server/main.py."""
import pytest
from server.main import _extract_image, _safe_header


class TestSafeHeader:
    def test_plain_ascii_unchanged(self):
        assert _safe_header("Hello world") == "Hello world"

    def test_newline_replaced_with_space(self):
        result = _safe_header("line1\nline2")
        assert "\n" not in result
        assert "line1" in result and "line2" in result

    def test_carriage_return_replaced_with_space(self):
        result = _safe_header("line1\rline2")
        assert "\r" not in result

    def test_crlf_injection_neutralised(self):
        evil = "value\r\nX-Injected: bad"
        result = _safe_header(evil)
        assert "\r\n" not in result
        assert "X-Injected" not in result or "X-Injected" in result.replace("\r\n", "  ")

    def test_non_ascii_replaced(self):
        result = _safe_header("héllo wörld")
        result.encode("ascii")  # raises ValueError if non-ASCII present

    def test_curly_apostrophe_normalised(self):
        assert "'" in _safe_header("it’s fine")
        assert "’" not in _safe_header("it’s fine")

    def test_curly_double_quotes_normalised(self):
        result = _safe_header("“hello”")
        assert '"' in result
        assert "“" not in result and "”" not in result

    def test_em_dash_becomes_hyphen(self):
        assert "-" in _safe_header("one—two")
        assert "—" not in _safe_header("one—two")

    def test_en_dash_becomes_hyphen(self):
        assert "-" in _safe_header("one–two")

    def test_multiple_spaces_collapsed(self):
        assert _safe_header("a  b   c") == "a b c"

    def test_empty_string_returns_empty(self):
        assert _safe_header("") == ""


class TestExtractImage:
    def test_returns_term_and_clean_text(self):
        text, term = _extract_image("Cats are cool. [IMAGE: fluffy cat]")
        assert term == "fluffy cat"
        assert "[IMAGE:" not in text
        assert "Cats are cool." in text

    def test_no_tag_returns_none_term(self):
        text, term = _extract_image("No image here.")
        assert term is None
        assert text == "No image here."

    def test_tag_only_returns_empty_text(self):
        text, term = _extract_image("[IMAGE: dinosaur]")
        assert term == "dinosaur"
        assert text == ""

    def test_case_insensitive(self):
        _, term = _extract_image("[image: elephant]")
        assert term == "elephant"

    def test_term_whitespace_trimmed(self):
        _, term = _extract_image("[IMAGE:   space panda   ]")
        assert term == "space panda"

    def test_tag_removed_from_middle_of_text(self):
        text, term = _extract_image("Here is [IMAGE: tiger] a fact.")
        assert term == "tiger"
        assert "[IMAGE:" not in text
        assert "Here is" in text and "a fact." in text

    def test_multiword_term_preserved(self):
        _, term = _extract_image("[IMAGE: blue whale swimming]")
        assert term == "blue whale swimming"
