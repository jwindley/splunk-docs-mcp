"""Tests for URL-handling utilities in crawler.py."""

import pytest
from splunk_docs_mcp.config import SOURCES_BY_ID
from splunk_docs_mcp.crawler import _normalise_url, _is_target_url, _section_from_url


ES = SOURCES_BY_ID["enterprise-security"]
ENTERPRISE = SOURCES_BY_ID["splunk-enterprise"]
LANTERN = SOURCES_BY_ID["lantern"]
ADMIN = SOURCES_BY_ID["admin-manual"]


# ---------------------------------------------------------------------------
# _normalise_url
# ---------------------------------------------------------------------------

class TestNormaliseUrl:
    def test_plain_url_unchanged(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5"
        assert _normalise_url(url) == url

    def test_fragment_stripped(self):
        result = _normalise_url("https://help.splunk.com/en/page#section-1")
        assert result == "https://help.splunk.com/en/page"
        assert "#" not in result

    def test_query_stripped(self):
        result = _normalise_url("https://help.splunk.com/en/page?action=edit")
        assert result == "https://help.splunk.com/en/page"
        assert "?" not in result

    def test_fragment_and_query_stripped(self):
        result = _normalise_url("https://help.splunk.com/en/page?foo=bar#anchor")
        assert result == "https://help.splunk.com/en/page"

    def test_mailto_returns_none(self):
        assert _normalise_url("mailto:user@example.com") is None

    def test_javascript_returns_none(self):
        assert _normalise_url("javascript:void(0)") is None

    def test_relative_returns_none(self):
        assert _normalise_url("/en/some/path") is None

    def test_http_accepted(self):
        url = "http://help.splunk.com/en/page"
        assert _normalise_url(url) == url


# ---------------------------------------------------------------------------
# _is_target_url — version filtering
# ---------------------------------------------------------------------------

class TestIsTargetUrlVersionFilter:
    def test_correct_es_version_accepted(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5/intro"
        assert _is_target_url(url, ES, None) is True

    def test_wrong_es_version_rejected(self):
        # ES 8.0 URL should be rejected when source.version == '8.5'
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.0/intro"
        assert _is_target_url(url, ES, None) is False

    def test_older_es_versions_rejected(self):
        for ver in ("8.1", "8.2", "8.3", "8.4"):
            url = f"https://help.splunk.com/en/splunk-enterprise-security-8/administer/{ver}/page"
            assert _is_target_url(url, ES, None) is False, f"Expected {ver} to be rejected"

    def test_no_version_segment_accepted(self):
        # A URL under the prefix but with no version segment (e.g. section index)
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide"
        assert _is_target_url(url, ES, None) is True

    def test_admin_manual_unaffected_by_version_filter(self):
        # admin-manual version is baked into url_prefix; no extra version segs expected
        url = (
            "https://help.splunk.com/en/data-management/splunk-enterprise-admin-manual"
            "/10.2/configuration-file-reference/transforms.conf"
        )
        assert _is_target_url(url, ADMIN, None) is True

    def test_lantern_no_version_segs_always_accepted(self):
        url = "https://lantern.splunk.com/Security_Use_Cases/Phishing/detection"
        assert _is_target_url(url, LANTERN, None) is True

    def test_wrong_prefix_rejected(self):
        url = "https://docs.splunk.com/Documentation/ES/8.5"
        assert _is_target_url(url, ES, None) is False

    def test_blocked_prefix_rejected(self):
        url = "https://help.splunk.com/api/something"
        assert _is_target_url(url, ES, None) is False

    def test_lantern_blocked_special_rejected(self):
        url = "https://lantern.splunk.com/Special:Search"
        assert _is_target_url(url, LANTERN, None) is False


# ---------------------------------------------------------------------------
# _is_target_url — section filter
# ---------------------------------------------------------------------------

class TestIsTargetUrlSectionFilter:
    def test_matching_section_accepted(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5/intro"
        assert _is_target_url(url, ES, "user-guide") is True

    def test_non_matching_section_rejected(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/administer/8.5/page"
        assert _is_target_url(url, ES, "user-guide") is False

    def test_section_index_passes_filter(self):
        # Section-level URL with no sub-path is allowed through (section matches)
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide"
        assert _is_target_url(url, ES, "user-guide") is True

    def test_lantern_section_filter(self):
        url = "https://lantern.splunk.com/Splunk_Success_Framework/something"
        assert _is_target_url(url, LANTERN, "Splunk_Success_Framework") is True
        assert _is_target_url(url, LANTERN, "Security_Use_Cases") is False


# ---------------------------------------------------------------------------
# _section_from_url
# ---------------------------------------------------------------------------

class TestSectionFromUrl:
    def test_es_url_returns_section(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5/intro"
        assert _section_from_url(url, ES) == "user-guide"

    def test_es_version_only_url_returns_none(self):
        # URL is just prefix + version — no section segment
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/8.5"
        assert _section_from_url(url, ES) is None

    def test_landing_page_returns_none(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8"
        assert _section_from_url(url, ES) is None

    def test_enterprise_section(self):
        url = "https://help.splunk.com/en/splunk-enterprise/administer/10.2/page"
        assert _section_from_url(url, ENTERPRISE) == "administer"

    def test_lantern_section(self):
        url = "https://lantern.splunk.com/Security_Use_Cases/Phishing/intro"
        assert _section_from_url(url, LANTERN) == "Security_Use_Cases"

    def test_lantern_root_returns_none(self):
        url = "https://lantern.splunk.com/"
        assert _section_from_url(url, LANTERN) is None
