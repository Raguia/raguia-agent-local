from raguia_local_agent.api_client import validate_api_base


def test_validate_api_base_accepts_https():
    assert validate_api_base("https://example.com/") == "https://example.com"


def test_validate_api_base_accepts_localhost_http():
    assert validate_api_base("http://localhost:8000") == "http://localhost:8000"


def test_validate_api_base_rejects_non_local_http():
    try:
        validate_api_base("http://example.com")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "https://" in str(e)


def test_validate_api_base_rejects_portal_page_url():
    try:
        validate_api_base("https://example.com/portal/acme")
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "racine" in str(e)
