"""P2 工具白名单与上限配置。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_url="mysql+aiomysql://user:pass@localhost/db",
        **overrides,
    )


def test_whitelist_parses_trims_and_lowercases() -> None:
    settings = make_settings(tools_enabled=" Calculator , document_search,web_search ,")
    assert settings.tools_enabled_names == {
        "calculator",
        "document_search",
        "web_search",
    }


def test_blank_whitelist_disables_all_tools() -> None:
    assert make_settings(tools_enabled="  , ").tools_enabled_names == frozenset()


def test_invalid_tool_name_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(tools_enabled="bad name")
    with pytest.raises(ValidationError):
        make_settings(tools_enabled="../etc/passwd")


@pytest.mark.parametrize("value", [0, -1, 61])
def test_timeout_bounds_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        make_settings(tool_timeout_seconds=value)


@pytest.mark.parametrize("value", [255, 50_001])
def test_output_cap_bounds_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(tool_max_output_chars=value)


def test_defaults_are_sane() -> None:
    settings = make_settings()
    assert settings.tools_enabled_names == {
        "calculator",
        "document_search",
        "web_search",
    }
    assert settings.tool_timeout_seconds == 15.0
    assert settings.tool_max_output_chars == 8000
