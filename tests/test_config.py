from __future__ import annotations

from pathlib import Path

import pytest

from core.config import ConfigurationError, PROJECT_ROOT, get_settings


def test_relative_paths_are_resolved_from_project_root() -> None:
    settings = get_settings(
        {
            "APP_ENV": "test",
            "REPORT_PATH": "data/report.pdf",
            "INDEX_PATH": "storage/test.joblib",
        }
    )
    assert settings.report_path == PROJECT_ROOT / "data/report.pdf"
    assert settings.index_path == PROJECT_ROOT / "storage/test.joblib"
    assert settings.is_testing


@pytest.mark.parametrize(
    ("name", "value"),
    [("APP_ENV", "staging"), ("TOP_K", "0"), ("MAX_REQUEST_BYTES", "abc")],
)
def test_invalid_configuration_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        get_settings({name: value})


def test_absolute_report_path_is_preserved(tmp_path: Path) -> None:
    report = tmp_path / "rapport.pdf"
    settings = get_settings({"APP_ENV": "test", "REPORT_PATH": str(report)})
    assert settings.report_path == report
