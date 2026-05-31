"""
test_doi_utils.py — Tests for doi_utils.py.

Covers:
- is_valid_doi: accept/reject patterns
- extract_doi_from_string: find DOI in free text
- get_doi_from_url: path, query param, and fallback patterns
- normalize_doi: lowercasing and whitespace
- doi_to_url: canonical URL generation
"""

from __future__ import annotations

import pytest

from zotero_restructuring.doi_utils import (
    doi_to_url,
    extract_doi_from_string,
    get_doi_from_url,
    is_valid_doi,
    normalize_doi,
)


class TestIsValidDoi:
    def test_valid_doi(self):
        assert is_valid_doi("10.1038/s41586-021-03819-2") is True

    def test_valid_doi_with_dots(self):
        assert is_valid_doi("10.1016/j.neuron.2023.01.001") is True

    def test_valid_doi_short_suffix(self):
        assert is_valid_doi("10.1371/journal.pbio.1002128") is True

    def test_invalid_doi_no_prefix(self):
        assert is_valid_doi("not-a-doi") is False

    def test_invalid_doi_wrong_prefix(self):
        assert is_valid_doi("11.1234/something") is False

    def test_invalid_doi_no_suffix(self):
        assert is_valid_doi("10.1234/") is False

    def test_empty_string(self):
        assert is_valid_doi("") is False

    def test_valid_doi_uppercase(self):
        assert is_valid_doi("10.1038/NATURE12345") is True


class TestExtractDoiFromString:
    def test_doi_in_plain_text(self):
        text = "See doi:10.1038/s41586-021-03819-2 for details."
        result = extract_doi_from_string(text)
        assert result == "10.1038/s41586-021-03819-2"

    def test_doi_in_url_string(self):
        text = "Available at https://doi.org/10.1016/j.neuron.2023.01.001"
        result = extract_doi_from_string(text)
        assert result == "10.1016/j.neuron.2023.01.001"

    def test_no_doi_in_string(self):
        assert extract_doi_from_string("No DOI here at all.") is None

    def test_doi_with_trailing_period_stripped(self):
        text = "Reference: 10.1038/nature12345."
        result = extract_doi_from_string(text)
        assert result is not None
        assert not result.endswith(".")

    def test_empty_string(self):
        assert extract_doi_from_string("") is None


class TestGetDoiFromUrl:
    def test_doi_org_url(self):
        url = "https://doi.org/10.1038/s41586-021-03819-2"
        result = get_doi_from_url(url)
        assert result == "10.1038/s41586-021-03819-2"

    def test_doi_in_path(self):
        url = "https://example.com/doi/10.1016/j.neuron.2023.01.001"
        result = get_doi_from_url(url)
        assert result == "10.1016/j.neuron.2023.01.001"

    def test_doi_in_query_param(self):
        url = "https://resolver.example.com/resolve?doi=10.1371/journal.pbio.1002128"
        result = get_doi_from_url(url)
        assert result == "10.1371/journal.pbio.1002128"

    def test_doi_in_url_string_fallback(self):
        url = "https://www.nature.com/articles/10.1038/nature12345"
        result = get_doi_from_url(url)
        assert result == "10.1038/nature12345"

    def test_no_doi_in_url(self):
        assert get_doi_from_url("https://example.com/noDOIhere") is None

    def test_empty_url(self):
        assert get_doi_from_url("") is None

    def test_none_like_empty(self):
        assert get_doi_from_url("") is None


class TestNormalizeDoi:
    def test_lowercase(self):
        assert normalize_doi("10.1038/NATURE") == "10.1038/nature"

    def test_strip_whitespace(self):
        assert normalize_doi("  10.1038/nature  ") == "10.1038/nature"


class TestDoiToUrl:
    def test_canonical_url(self):
        assert doi_to_url("10.1038/nature12345") == "https://doi.org/10.1038/nature12345"

    def test_strips_whitespace(self):
        assert doi_to_url("  10.1038/nature12345  ") == "https://doi.org/10.1038/nature12345"
