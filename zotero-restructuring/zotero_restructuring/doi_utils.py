"""
doi_utils.py — DOI extraction utilities.

Refactored from the existing doi_extract.py.  This module is pure utility:
no network requests, no script-level execution.  All functions are importable
and testable in isolation.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# DOI pattern: starts with 10., 4-9 digit registrant code, slash, suffix
_DOI_PATTERN = re.compile(
    r"10\.\d{4,9}/[-._;()/:A-Z0-9]+",
    re.IGNORECASE,
)

# Clean up trailing punctuation that is unlikely to be part of the DOI
_TRAILING_JUNK = re.compile(r"[.,;:)\]>\"']+$")


def is_valid_doi(doi: str) -> bool:
    """Return True if doi matches the standard DOI format."""
    return bool(_DOI_PATTERN.fullmatch(doi.strip()))


def extract_doi_from_string(text: str) -> str | None:
    """
    Find the first DOI-like pattern in an arbitrary text string.

    Returns the DOI string, or None if not found.
    """
    match = _DOI_PATTERN.search(text)
    if not match:
        return None
    doi = match.group(0)
    doi = _TRAILING_JUNK.sub("", doi)
    return doi if doi else None


def get_doi_from_url(url: str) -> str | None:
    """
    Extract a DOI from a URL without making any network requests.

    Checks:
    1. 'doi' segment in URL path (e.g. .../doi/10.1000/xyz)
    2. 'doi' query parameter (e.g. ?doi=10.1000/xyz)
    3. DOI pattern anywhere in the URL string
    """
    if not url:
        return None

    parsed = urlparse(url)
    path_parts = parsed.path.split("/")

    # Check path for 'doi' segment
    for i, part in enumerate(path_parts):
        if part.lower() == "doi" and i + 1 < len(path_parts):
            candidate = "/".join(path_parts[i + 1:])
            candidate = _TRAILING_JUNK.sub("", candidate)
            if is_valid_doi(candidate):
                return candidate

    # Check query parameters
    params = parse_qs(parsed.query)
    for key in ("doi", "DOI"):
        if key in params:
            candidate = params[key][0]
            candidate = _TRAILING_JUNK.sub("", candidate)
            if is_valid_doi(candidate):
                return candidate

    # Fallback: scan entire URL for DOI pattern
    return extract_doi_from_string(url)


def normalize_doi(doi: str) -> str:
    """
    Return a lowercase, whitespace-stripped DOI string.

    Does not validate format; use is_valid_doi() for that.
    """
    return doi.strip().lower()


def doi_to_url(doi: str) -> str:
    """Return the canonical https://doi.org/ URL for a given DOI."""
    return f"https://doi.org/{doi.strip()}"
