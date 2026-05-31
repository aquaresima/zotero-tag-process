"""
test_config.py -- Tests for config.py.

Covers:
- get_zotero_sqlite_path with and without env var
- get_zotero_api_key / get_openai_api_key
- require_zotero_api_key / require_openai_api_key (missing key -> error)
- validate_write_path (allowed and disallowed paths)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from zotero_restructuring.config import (
    DATA_DIR,
    SIMULATION_DB_PATH,
    get_openai_api_key,
    get_zotero_api_key,
    get_zotero_sqlite_path,
    require_openai_api_key,
    require_zotero_api_key,
    validate_write_path,
)


class TestGetZoteroSqlitePath:
    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove env var if it exists
            os.environ.pop("ZOTERO_SQLITE_PATH", None)
            path = get_zotero_sqlite_path()
            assert path == Path.home() / "Zotero" / "zotero.sqlite"

    def test_env_var_override(self):
        with patch.dict(os.environ, {"ZOTERO_SQLITE_PATH": "/tmp/custom.sqlite"}):
            path = get_zotero_sqlite_path()
            assert path == Path("/tmp/custom.sqlite").resolve()


class TestApiKeys:
    def test_get_zotero_api_key_present(self):
        with patch.dict(os.environ, {"ZOTERO_API_KEY": "test-key-123"}):
            assert get_zotero_api_key() == "test-key-123"

    def test_get_zotero_api_key_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOTERO_API_KEY", None)
            assert get_zotero_api_key() is None

    def test_get_openai_api_key_present(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            assert get_openai_api_key() == "sk-test"

    def test_get_openai_api_key_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            assert get_openai_api_key() is None

    def test_require_zotero_api_key_present(self):
        with patch.dict(os.environ, {"ZOTERO_API_KEY": "key-val"}):
            assert require_zotero_api_key() == "key-val"

    def test_require_zotero_api_key_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOTERO_API_KEY", None)
            with pytest.raises(EnvironmentError) as exc_info:
                require_zotero_api_key()
            assert "ZOTERO_API_KEY" in str(exc_info.value)

    def test_require_openai_api_key_present(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-val"}):
            assert require_openai_api_key() == "sk-val"

    def test_require_openai_api_key_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with pytest.raises(EnvironmentError) as exc_info:
                require_openai_api_key()
            assert "OPENAI_API_KEY" in str(exc_info.value)


class TestValidateWritePath:
    def test_allowed_data_dir(self):
        # Should not raise
        validate_write_path(DATA_DIR / "test.sqlite")

    def test_allowed_simulation_path(self):
        validate_write_path(SIMULATION_DB_PATH)

    def test_disallowed_path(self):
        with pytest.raises(PermissionError):
            validate_write_path(Path("/tmp/not_allowed/file.sqlite"))

    def test_disallowed_home_path(self):
        with pytest.raises(PermissionError):
            validate_write_path(Path.home() / "something.sqlite")
