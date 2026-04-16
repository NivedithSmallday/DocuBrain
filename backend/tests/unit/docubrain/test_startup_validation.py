"""Tests for startup validation in no-vector-DB mode.

Verifies that DISABLE_VECTOR_DB raises RuntimeError when combined with
incompatible settings (MULTI_TENANT, ENABLE_CRAFT).
"""

from unittest.mock import patch

import pytest


class TestValidateNoVectorDbSettings:
    @patch("docubrain.main.DISABLE_VECTOR_DB", False)
    def test_no_error_when_vector_db_enabled(self) -> None:
        from docubrain.main import validate_no_vector_db_settings

        validate_no_vector_db_settings()

    @patch("docubrain.main.DISABLE_VECTOR_DB", True)
    @patch("docubrain.main.MULTI_TENANT", False)
    @patch("docubrain.server.features.build.configs.ENABLE_CRAFT", False)
    def test_no_error_when_no_conflicts(self) -> None:
        from docubrain.main import validate_no_vector_db_settings

        validate_no_vector_db_settings()

    @patch("docubrain.main.DISABLE_VECTOR_DB", True)
    @patch("docubrain.main.MULTI_TENANT", True)
    def test_raises_on_multi_tenant(self) -> None:
        from docubrain.main import validate_no_vector_db_settings

        with pytest.raises(RuntimeError, match="MULTI_TENANT"):
            validate_no_vector_db_settings()

    @patch("docubrain.main.DISABLE_VECTOR_DB", True)
    @patch("docubrain.main.MULTI_TENANT", False)
    @patch("docubrain.server.features.build.configs.ENABLE_CRAFT", True)
    def test_raises_on_enable_craft(self) -> None:
        from docubrain.main import validate_no_vector_db_settings

        with pytest.raises(RuntimeError, match="ENABLE_CRAFT"):
            validate_no_vector_db_settings()

    @patch("docubrain.main.DISABLE_VECTOR_DB", True)
    @patch("docubrain.main.MULTI_TENANT", True)
    @patch("docubrain.server.features.build.configs.ENABLE_CRAFT", True)
    def test_multi_tenant_checked_before_craft(self) -> None:
        """MULTI_TENANT is checked first, so it should be the error raised."""
        from docubrain.main import validate_no_vector_db_settings

        with pytest.raises(RuntimeError, match="MULTI_TENANT"):
            validate_no_vector_db_settings()
