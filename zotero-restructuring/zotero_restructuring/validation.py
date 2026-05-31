"""
validation.py — Consistency checks for Library and simulation state.

Checks:
- No collection hierarchy cycles (raises ValidationError)
- Orphaned items (warning in report)
- Empty collections (warning in report)
- Tag integrity (items reference existing tags)
- Collection integrity (items reference existing collections)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .library import Library


class ValidationError(Exception):
    """Raised when a fatal consistency violation is detected (e.g., cycle)."""


@dataclass
class ValidationReport:
    """Result of a full consistency check."""

    orphaned_item_keys: list[str] = field(default_factory=list)
    empty_collection_names: list[str] = field(default_factory=list)
    dangling_tag_refs: list[tuple[int, int]] = field(default_factory=list)  # (item_id, tag_id)
    dangling_collection_refs: list[tuple[int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_valid: bool = True

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self) -> str:
        lines = [f"ValidationReport: {'OK' if self.is_valid else 'WARNINGS'}"]
        if self.orphaned_item_keys:
            lines.append(
                f"  Orphaned items ({len(self.orphaned_item_keys)}): "
                f"{self.orphaned_item_keys[:5]}{'...' if len(self.orphaned_item_keys) > 5 else ''}"
            )
        if self.empty_collection_names:
            lines.append(
                f"  Empty collections ({len(self.empty_collection_names)}): "
                f"{self.empty_collection_names[:5]}"
            )
        if self.dangling_tag_refs:
            lines.append(f"  Dangling tag refs: {len(self.dangling_tag_refs)}")
        if self.dangling_collection_refs:
            lines.append(f"  Dangling collection refs: {len(self.dangling_collection_refs)}")
        for w in self.warnings:
            lines.append(f"  Warning: {w}")
        return "\n".join(lines)


def _detect_cycle(collection_id: int, parent_map: dict[int, int | None]) -> bool:
    """Return True if following parent pointers from collection_id forms a cycle."""
    visited: set[int] = set()
    current: int | None = collection_id
    while current is not None:
        if current in visited:
            return True
        visited.add(current)
        current = parent_map.get(current)
    return False


def validate(library: "Library") -> ValidationReport:
    """
    Run all consistency checks against a Library.

    Parameters
    ----------
    library:
        The in-memory Library (or one reconstituted from simulation state).

    Returns
    -------
    ValidationReport
        Contains warnings for non-fatal issues.

    Raises
    ------
    ValidationError
        If a collection hierarchy cycle is detected.
    """
    report = ValidationReport()

    # 1. Detect collection hierarchy cycles
    parent_map: dict[int, int | None] = {
        cid: col.parent_id for cid, col in library.collections.items()
    }
    for cid in library.collections:
        if _detect_cycle(cid, parent_map):
            raise ValidationError(
                f"Collection hierarchy cycle detected involving collectionID={cid}. "
                "Fix parent references before proceeding."
            )

    # 2. Orphaned items (items not in any collection)
    orphans = library.orphaned_items()
    if orphans:
        report.orphaned_item_keys = [item.key for item in orphans]
        report.add_warning(
            f"{len(orphans)} orphaned item(s) not assigned to any collection. "
            f"First keys: {report.orphaned_item_keys[:3]}"
        )

    # 3. Empty collections
    empty = [
        col.name
        for col in library.collections.values()
        if not col.items
    ]
    if empty:
        report.empty_collection_names = empty
        report.add_warning(f"{len(empty)} collection(s) are empty.")

    # 4. Dangling tag references (item references a tag not in library.tags)
    for iid, item in library.items.items():
        for tid in item.tags:
            if tid not in library.tags:
                report.dangling_tag_refs.append((iid, tid))
    if report.dangling_tag_refs:
        report.add_warning(
            f"{len(report.dangling_tag_refs)} dangling item->tag reference(s)."
        )

    # 5. Dangling collection references (item references a collection not in library.collections)
    for iid, item in library.items.items():
        for cid in item.collections:
            if cid not in library.collections:
                report.dangling_collection_refs.append((iid, cid))
    if report.dangling_collection_refs:
        report.add_warning(
            f"{len(report.dangling_collection_refs)} dangling item->collection reference(s)."
        )

    return report
