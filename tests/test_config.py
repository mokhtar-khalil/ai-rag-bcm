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


def test_openai_key_is_required_in_production_for_the_openai_provider() -> None:
    with pytest.raises(ConfigurationError):
        get_settings(
            {
                "APP_ENV": "production",
                "GENERATION_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
                "REINDEX_TOKEN": "secret",
            }
        )


def test_openai_key_is_not_required_outside_production() -> None:
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "openai", "OPENAI_API_KEY": ""}
    )
    assert settings.generation_provider == "openai"
    assert settings.openai_api_key == ""


def test_cors_allowed_origins_parses_comma_separated_list() -> None:
    settings = get_settings(
        {"APP_ENV": "test", "CORS_ALLOWED_ORIGINS": "https://bcm.mr, https://www.bcm.mr"}
    )
    assert settings.cors_allowed_origins == ("https://bcm.mr", "https://www.bcm.mr")


def test_cors_allowed_origins_defaults_to_empty() -> None:
    settings = get_settings({"APP_ENV": "test"})
    assert settings.cors_allowed_origins == ()


def test_gemini_key_is_required_in_production_for_the_gemini_provider() -> None:
    with pytest.raises(ConfigurationError):
        get_settings(
            {
                "APP_ENV": "production",
                "GENERATION_PROVIDER": "gemini",
                "GEMINI_API_KEY": "",
                "REINDEX_TOKEN": "secret",
            }
        )


def test_gemini_key_is_not_required_outside_production() -> None:
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "gemini", "GEMINI_API_KEY": ""}
    )
    assert settings.generation_provider == "gemini"
    assert settings.gemini_api_key == ""
