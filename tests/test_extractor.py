"""Tests for parse_url_metadata() in extractor.py."""

import pytest
from splunk_docs_mcp.config import SOURCES_BY_ID
from splunk_docs_mcp.extractor import parse_url_metadata


ES = SOURCES_BY_ID["enterprise-security"]
ADMIN = SOURCES_BY_ID["admin-manual"]
ENTERPRISE = SOURCES_BY_ID["splunk-enterprise"]
CLOUD = SOURCES_BY_ID["splunk-cloud"]
LANTERN = SOURCES_BY_ID["lantern"]


class TestParseUrlMetadataES:
    def test_full_depth(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5/introduction/about-ses"
        meta = parse_url_metadata(url, ES)
        assert meta["section"] == "user-guide"
        assert meta["subsection"] == "introduction"
        assert meta["slug"] == "about-ses"

    def test_version_segment_stripped(self):
        # Version segment '8.5' should not appear in section/subsection/slug
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/administer/8.5/configure/overview"
        meta = parse_url_metadata(url, ES)
        assert meta["section"] == "administer"
        assert meta["subsection"] == "configure"
        assert meta["slug"] == "overview"

    def test_section_only(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5"
        meta = parse_url_metadata(url, ES)
        assert meta["section"] == "user-guide"
        assert meta["subsection"] is None
        assert meta["slug"] == "user-guide"

    def test_landing_page(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8"
        meta = parse_url_metadata(url, ES)
        assert meta["section"] is None
        assert meta["subsection"] is None
        assert meta["slug"] is None


class TestParseUrlMetadataAdminManual:
    def test_conf_file(self):
        url = (
            "https://help.splunk.com/en/data-management/splunk-enterprise-admin-manual"
            "/10.2/configuration-file-reference/transforms.conf"
        )
        meta = parse_url_metadata(url, ADMIN)
        assert meta["section"] == "transforms.conf"
        assert meta["subsection"] is None
        assert meta["slug"] == "transforms.conf"

    def test_index_page(self):
        url = (
            "https://help.splunk.com/en/data-management/splunk-enterprise-admin-manual"
            "/10.2/configuration-file-reference/10.2.0-configuration-file-reference"
        )
        meta = parse_url_metadata(url, ADMIN)
        # The slug segment has a hyphen so it is NOT treated as a pure version segment
        # (version regex requires purely numeric dots, e.g. '10.2.0' not '10.2.0-text')
        assert meta["slug"] == "10.2.0-configuration-file-reference"


class TestParseUrlMetadataLantern:
    def test_three_levels(self):
        url = "https://lantern.splunk.com/Security_Use_Cases/Authentication/about-auth"
        meta = parse_url_metadata(url, LANTERN)
        assert meta["section"] == "Security_Use_Cases"
        assert meta["subsection"] == "Authentication"
        assert meta["slug"] == "about-auth"

    def test_four_levels(self):
        # Level 3 group is not stored as subsection — last segment is slug
        url = "https://lantern.splunk.com/Splunk_Success_Framework/Optimizing_storage/Archived_data/cold-to-frozen"
        meta = parse_url_metadata(url, LANTERN)
        assert meta["section"] == "Splunk_Success_Framework"
        assert meta["subsection"] == "Optimizing_storage"
        assert meta["slug"] == "cold-to-frozen"

    def test_root(self):
        url = "https://lantern.splunk.com/"
        meta = parse_url_metadata(url, LANTERN)
        assert meta["section"] is None
        assert meta["slug"] is None
