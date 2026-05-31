"""
tags.py — Tag normalization engine.

Normalizes tags: lowercase, strip punctuation, singularize, deduplicate.
Detects collisions (two distinct tags normalize to the same string) and
preserves both as merge candidates rather than auto-merging.
Categorizes tags using rule-based matching from config.py.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field

import inflect

from .config import (
    TAG_DOMAIN_KEYWORDS,
    TAG_METHOD_KEYWORDS,
    TAG_PROTECTED,
    TAG_QUALITY_VALUES,
    TAG_STATUS_VALUES,
)

_INFLECT = inflect.engine()


# ── Junk / non-substantive tag detection ─────────────────────────────────────
# (moved here from the removed worker.py so simulation.py, stats.py and
#  graph.py have a stable home for these pure heuristics.)

_NUM_RE = re.compile(r"^\d+([.,]\d+)?$")
_CHEM_RE = re.compile(r"^(?:[A-Z][a-z]?\d*){2,}$")


def is_junk_tag(name: str) -> bool:
    """Return True for tags that are not substantive (numbers, single chars, formulae)."""
    s = name.strip()
    if len(s) <= 1:
        return True
    if _NUM_RE.match(s):
        return True
    if _CHEM_RE.match(s):
        return True
    return False


def is_substantive_tag(name: str, excluded: set[str] | None = None) -> bool:
    """Return True if a tag should count toward an item being 'tagged'.

    Checks the DB-managed excluded set first (lowercase strings), then config
    status/quality/protected sets, then junk heuristics.
    """
    lowered = name.strip().lower()
    if excluded is not None and lowered in excluded:
        return False
    if lowered in TAG_STATUS_VALUES:
        return False
    if lowered in TAG_QUALITY_VALUES:
        return False
    if lowered in TAG_PROTECTED:
        return False
    if is_junk_tag(name):
        return False
    return True

# Punctuation characters to strip, preserving hyphens within words
_STRIP_PUNCT = re.compile(r"[^\w\s-]")
# Collapse whitespace (after hyphen removal or expansion)
_WHITESPACE = re.compile(r"\s+")


# ── Normalization helpers ─────────────────────────────────────────────────────

def _replace_hyphens_with_spaces(text: str) -> str:
    """Convert hyphens/underscores used as word separators to spaces."""
    return text.replace("-", " ").replace("_", " ")


def _strip_punctuation(text: str) -> str:
    """Remove non-word, non-space characters."""
    return _STRIP_PUNCT.sub("", text)


def _singularize(word: str) -> str:
    """Return singular form of word if it is plural, else return word unchanged."""
    singular = _INFLECT.singular_noun(word)
    return singular if singular else word


def normalize_tag(raw: str) -> str:
    """
    Normalize a single tag name to a canonical form.

    Steps:
    1. Lowercase
    2. Replace hyphens/underscores with spaces
    3. Strip remaining non-word punctuation
    4. Collapse whitespace
    5. Singularize each word
    """
    text = raw.lower()
    text = _replace_hyphens_with_spaces(text)
    text = _strip_punctuation(text)
    text = _WHITESPACE.sub(" ", text).strip()
    words = [_singularize(w) for w in text.split()]
    return " ".join(words)


# ── Categorization ────────────────────────────────────────────────────────────

def categorize_tag(normalized: str) -> str | None:
    """
    Return a category string for a normalized tag, or None if uncategorized.

    Priority: status > quality > method > domain.
    """
    lower = normalized.lower()

    if lower in TAG_STATUS_VALUES:
        return "status"
    if lower in TAG_QUALITY_VALUES:
        return "quality"
    for category, keywords in TAG_METHOD_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return f"method:{category}"
    for category, keywords in TAG_DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return f"domain:{category}"
    return None


# ── Collision detection & normalization result ────────────────────────────────

@dataclass
class NormalizedTag:
    """A tag after normalization, with provenance information."""

    original_names: list[str]          # all original names that map here
    normalized_name: str
    category: str | None
    is_merge_candidate: bool = False   # True when multiple originals collide


@dataclass
class NormalizationReport:
    """Result of running normalize_tags() on a collection of tag names."""

    normalized: dict[str, NormalizedTag]          # normalized_name -> NormalizedTag
    merge_candidates: list[NormalizedTag] = field(default_factory=list)
    protected_skipped: list[str] = field(default_factory=list)

    @property
    def canonical_names(self) -> list[str]:
        return sorted(self.normalized.keys())


def normalize_tags(tag_names: list[str]) -> NormalizationReport:
    """
    Normalize a list of tag names and detect merge candidates.

    A merge candidate is any normalized form that was produced by more than
    one distinct original name.  Both originals are preserved; neither is
    auto-merged.

    Parameters
    ----------
    tag_names:
        List of raw tag name strings (may contain duplicates).

    Returns
    -------
    NormalizationReport
        Contains the normalized tags dict and a list of merge candidates.
    """
    # normalized_name -> list of distinct original names
    collision_map: dict[str, list[str]] = {}
    protected_skipped: list[str] = []

    for raw in tag_names:
        lower_raw = raw.lower().strip()
        if lower_raw in TAG_PROTECTED:
            protected_skipped.append(raw)
            continue

        norm = normalize_tag(raw)
        if not norm:
            continue

        existing = collision_map.setdefault(norm, [])
        if raw not in existing:
            existing.append(raw)

    normalized: dict[str, NormalizedTag] = {}
    merge_candidates: list[NormalizedTag] = []

    for norm, originals in collision_map.items():
        is_collision = len(originals) > 1
        nt = NormalizedTag(
            original_names=originals,
            normalized_name=norm,
            category=categorize_tag(norm),
            is_merge_candidate=is_collision,
        )
        normalized[norm] = nt
        if is_collision:
            merge_candidates.append(nt)

    return NormalizationReport(
        normalized=normalized,
        merge_candidates=merge_candidates,
        protected_skipped=protected_skipped,
    )
