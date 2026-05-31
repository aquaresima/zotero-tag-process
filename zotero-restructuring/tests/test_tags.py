"""
test_tags.py — Tests for tags.py.

Covers:
- normalize_tag: individual transformations
- normalize_tags: collision detection (Scenario C)
- categorize_tag: domain/method/status/quality categories
- Protected tags are skipped
"""

from __future__ import annotations

import pytest

from zotero_restructuring.tags import (
    NormalizationReport,
    NormalizedTag,
    categorize_tag,
    normalize_tag,
    normalize_tags,
)


class TestNormalizeTag:
    def test_lowercase(self):
        assert normalize_tag("NEURON") == "neuron"

    def test_hyphen_to_space(self):
        assert normalize_tag("neural-networks") == "neural network"

    def test_underscore_to_space(self):
        assert normalize_tag("machine_learning") == "machine learning"

    def test_punctuation_stripped(self):
        assert normalize_tag("spiking!") == "spiking"

    def test_singularize_simple(self):
        # "neurons" -> "neuron"
        result = normalize_tag("neurons")
        assert result == "neuron"

    def test_singularize_plural_phrase(self):
        result = normalize_tag("neural networks")
        assert result == "neural network"

    def test_whitespace_collapsed(self):
        assert normalize_tag("  deep   learning  ") == "deep learning"


# ── Scenario C ────────────────────────────────────────────────────────────────

class TestNormalizationWithCollisions:
    """Scenario C: six forms all normalize to two canonical tags."""

    def test_normalization_with_collisions(self):
        """
        Scenario C: ["Neurons","neurons","neuron","NEURON",
                     "neural networks","neural-networks"]
        -> canonical set: {"neuron", "neural network"}
        """
        input_tags = [
            "Neurons", "neurons", "neuron", "NEURON",
            "neural networks", "neural-networks",
        ]
        report = normalize_tags(input_tags)

        assert set(report.canonical_names) == {"neuron", "neural network"}, (
            f"Canonical names: {report.canonical_names}"
        )

    def test_collision_flagged_as_merge_candidate(self):
        input_tags = ["Neurons", "neurons", "neuron", "NEURON"]
        report = normalize_tags(input_tags)

        neuron_entry = report.normalized.get("neuron")
        assert neuron_entry is not None
        assert neuron_entry.is_merge_candidate is True

    def test_merge_candidate_preserves_original_forms(self):
        input_tags = ["Neurons", "neurons", "neuron", "NEURON"]
        report = normalize_tags(input_tags)

        neuron_entry = report.normalized["neuron"]
        originals = set(neuron_entry.original_names)
        # All four distinct originals should be preserved
        assert originals == {"Neurons", "neurons", "neuron", "NEURON"}, (
            f"Original forms: {originals}"
        )

    def test_non_colliding_tag_not_flagged(self):
        input_tags = ["simulation", "review"]
        report = normalize_tags(input_tags)
        assert report.merge_candidates == []

    def test_empty_input(self):
        report = normalize_tags([])
        assert report.normalized == {}
        assert report.merge_candidates == []


class TestCategorizeTag:
    def test_status_category(self):
        assert categorize_tag("unread") == "status"
        assert categorize_tag("reading") == "status"
        assert categorize_tag("read") == "status"

    def test_quality_category(self):
        assert categorize_tag("important") == "quality"
        assert categorize_tag("seminal") == "quality"

    def test_domain_neuroscience(self):
        cat = categorize_tag("neuron")
        assert cat is not None and "neuroscience" in cat

    def test_domain_machine_learning(self):
        cat = categorize_tag("deep learning")
        assert cat is not None and "machine-learning" in cat

    def test_method_simulation(self):
        cat = categorize_tag("spiking network simulation")
        assert cat is not None and "simulation" in cat

    def test_uncategorized(self):
        assert categorize_tag("xyzzy") is None


class TestProtectedTags:
    def test_protected_tags_skipped(self):
        inputs = ["neuron", "⛔ No DOI found", "#nosource"]
        report = normalize_tags(inputs)
        assert "neuron" in report.canonical_names
        # Protected tags should NOT appear in normalized output
        for name in report.canonical_names:
            assert "no doi" not in name.lower()
            assert "nosource" not in name.lower()
        assert len(report.protected_skipped) >= 1

    def test_duplicate_inputs_deduped(self):
        """Exact duplicate inputs should not double-count originals."""
        inputs = ["neuron", "neuron", "neuron"]
        report = normalize_tags(inputs)
        entry = report.normalized["neuron"]
        # Only one distinct original
        assert entry.original_names == ["neuron"]
        assert entry.is_merge_candidate is False
