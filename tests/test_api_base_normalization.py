"""Tests : tolérance aux suffixes /api ou /api/portal copiés-collés."""

from __future__ import annotations

from raguia_local_agent.api_client import validate_api_base


def test_strips_trailing_api_suffix():
    assert validate_api_base("https://example.com/api") == "https://example.com"


def test_strips_trailing_api_suffix_with_slash():
    assert validate_api_base("https://example.com/api/") == "https://example.com"


def test_strips_trailing_api_portal_suffix():
    assert validate_api_base("https://example.com/api/portal") == "https://example.com"


def test_strips_trailing_api_portal_suffix_with_slash():
    assert validate_api_base("https://example.com/api/portal/") == "https://example.com"


def test_does_not_strip_unrelated_path():
    assert validate_api_base("https://example.com/raguia") == "https://example.com/raguia"


def test_keeps_clean_root_url_unchanged():
    assert validate_api_base("https://example.com") == "https://example.com"
