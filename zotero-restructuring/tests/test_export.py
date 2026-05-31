"""
test_export.py -- Tests for export.py (Phase 4 skeleton).

Covers:
- export_to_sqlite raises NotImplementedError
- upload_to_zotero_api raises NotImplementedError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zotero_restructuring.export import export_to_sqlite, upload_to_zotero_api


class TestExportSkeleton:
    def test_export_to_sqlite_not_implemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            export_to_sqlite(sim=None, output_path=Path("/tmp/test.sqlite"))
        assert "Phase 4" in str(exc_info.value)

    def test_upload_to_zotero_api_not_implemented(self):
        with pytest.raises(NotImplementedError) as exc_info:
            upload_to_zotero_api(sim=None, validation_report=None)
        assert "Phase 4" in str(exc_info.value)
