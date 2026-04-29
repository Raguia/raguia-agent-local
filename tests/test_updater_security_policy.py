import httpx

from raguia_local_agent.updater import (
    _extract_allowed_hosts,
    _validate_download_chain_https_and_hosts,
)


def test_extract_allowed_hosts_includes_api_and_github_defaults():
    hosts = _extract_allowed_hosts({}, "portal.example.com")
    assert "portal.example.com" in hosts
    assert "github.com" in hosts
    assert "objects.githubusercontent.com" in hosts


def test_extract_allowed_hosts_accepts_custom_hosts_from_api():
    hosts = _extract_allowed_hosts(
        {"allowed_download_hosts": ["downloads.example.net", "cdn.example.net"]},
        "portal.example.com",
    )
    assert "portal.example.com" in hosts
    assert "downloads.example.net" in hosts
    assert "cdn.example.net" in hosts


def test_validate_download_chain_rejects_non_https():
    allowed = {"portal.example.com"}
    req = httpx.Request("GET", "http://portal.example.com/agent.exe")
    resp = httpx.Response(200, request=req)
    assert _validate_download_chain_https_and_hosts([resp], allowed) is False


def test_validate_download_chain_rejects_unapproved_host():
    allowed = {"portal.example.com"}
    req = httpx.Request("GET", "https://evil.example.net/agent.exe")
    resp = httpx.Response(200, request=req)
    assert _validate_download_chain_https_and_hosts([resp], allowed) is False


def test_validate_download_chain_accepts_https_allowed_redirect_chain():
    allowed = {"github.com", "objects.githubusercontent.com"}
    req1 = httpx.Request("GET", "https://github.com/org/repo/releases/download/v1/agent.exe")
    req2 = httpx.Request("GET", "https://objects.githubusercontent.com/asset.bin")
    redirect = httpx.Response(302, request=req1, headers={"location": str(req2.url)})
    final = httpx.Response(200, request=req2, content=b"binary")
    assert _validate_download_chain_https_and_hosts([redirect, final], allowed) is True
