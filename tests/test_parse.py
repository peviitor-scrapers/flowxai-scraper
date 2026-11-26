import logging
import re
from datetime import datetime, timezone

from scraper import parse as parse_module
from scraper.parse import (
    iso_z,
    location_from_title,
    normalize_workmode,
    parse_deadline,
    parse_listing,
    slugify,
    validate_ro_locations,
)


def test_slugify_strips_diacritics():
    assert slugify("Key Account Manager – Vânzări Distribuitori") == "key-account-manager-vanzari-distribuitori"
    assert slugify("  Operator   exploatare și mentenanță  ") == "operator-exploatare-si-mentenanta"


def test_parse_deadline():
    assert parse_deadline("Apply by: 30.09.2026").startswith("2026-09-30T23:59:59")
    assert parse_deadline("2026-10-31").startswith("2026-10-31T00:00:00")
    assert parse_deadline("no deadline") is None
    assert parse_deadline(None) is None


# peviitor's Solr date fields parse only "...SSSZ" (millisecond precision,
# literal Z) -- the shape JS's Date.toISOString() emits natively. Python's own
# datetime.isoformat() instead emits microseconds + "+00:00", which Solr
# rejects with a 400. Both iso_z() and everything built on it must produce the
# Solr-safe shape, not Python's native one.
_ISO_Z_RX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_iso_z_matches_solr_date_format_not_pythons_native_isoformat():
    formatted = iso_z(datetime(2026, 9, 30, 23, 59, 59, 123456, tzinfo=timezone.utc))
    assert formatted == "2026-09-30T23:59:59.123Z"
    assert _ISO_Z_RX.match(formatted)


def test_parse_deadline_emits_solr_safe_z_format():
    assert _ISO_Z_RX.match(parse_deadline("Apply by: 30.09.2026"))
    assert _ISO_Z_RX.match(parse_deadline("2026-10-31"))


class _FakeResponse:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload, self.ok, self.status_code = payload, ok, status_code

    def json(self):
        return self._payload


def _workable(monkeypatch, payload, ok=True):
    monkeypatch.setattr(parse_module.fetch, "get", lambda *a, **k: _FakeResponse(payload, ok, 200 if ok else 500))


class TestParseListingWorkable:
    """FLOWX.AI is read from the Workable widget API (the HTML listing path of
    the template does not apply), so the HTML argument is ignored."""

    def test_keeps_only_romanian_postings(self, monkeypatch):
        _workable(monkeypatch, {"jobs": [
            {"title": "Business Analyst", "url": "https://apply.workable.com/j/1", "country": "Romania", "city": "Cluj-Napoca"},
            {"title": "US Account Exec", "url": "https://apply.workable.com/j/2", "country": "United States", "city": "Austin"},
        ]})
        items = parse_listing("")
        assert [i["title"] for i in items] == ["Business Analyst"]
        assert items[0]["url"] == "https://apply.workable.com/j/1"
        assert items[0]["city"] == ["Cluj-Napoca"]

    def test_accepts_the_ro_country_code(self, monkeypatch):
        _workable(monkeypatch, {"jobs": [{"title": "QA", "url": "https://apply.workable.com/j/3", "country": "RO", "city": "Iasi"}]})
        assert [i["title"] for i in parse_listing("")] == ["QA"]

    def test_defaults_the_city_when_the_posting_has_none(self, monkeypatch):
        _workable(monkeypatch, {"jobs": [{"title": "QA", "url": "https://apply.workable.com/j/3", "country": "Romania"}]})
        items = parse_listing("")
        assert len(items) == 1 and len(items[0]["city"]) == 1 and items[0]["city"][0]

    def test_skips_postings_without_title_or_url(self, monkeypatch):
        _workable(monkeypatch, {"jobs": [
            {"title": "", "url": "https://apply.workable.com/j/4", "country": "Romania"},
            {"title": "No URL", "url": "", "country": "Romania"},
        ]})
        assert parse_listing("") == []

    def test_returns_empty_when_the_api_errors(self, monkeypatch):
        _workable(monkeypatch, {}, ok=False)
        assert parse_listing("") == []

    def test_returns_empty_when_there_are_no_jobs(self, monkeypatch):
        _workable(monkeypatch, {"jobs": []})
        assert parse_listing("") == []


class TestLocationFromTitle:
    def test_returns_the_default_location(self):
        # FLOWX.AI has no city hint in titles; the default location is used as is.
        assert location_from_title("Software Engineer - Cluj", ["Default"]) == ["Default"]


class TestValidateRoLocations:
    """Parity port of scraper-js's transformJobsForSOLR location step -- the
    JS/Python gap this closes: scraper-py previously had no allowlist at all,
    so any location (or none) silently uploaded as-is instead of degrading to
    the generic "România" fallback."""

    def test_falls_back_when_location_is_missing_entirely(self):
        assert validate_ro_locations(None) == ["România"]
        assert validate_ro_locations([]) == ["România"]

class TestNormalizeWorkmode:
    """Parity port of scraper-js's normalizeWorkmode."""

    def test_maps_remote_variants(self):
        assert normalize_workmode("Full Remote") == "remote"

    def test_maps_office_or_site_variants(self):
        assert normalize_workmode("On-site") == "on-site"
        assert normalize_workmode("Office") == "on-site"

    def test_defaults_unrecognized_values_to_hybrid(self):
        assert normalize_workmode("Flexible") == "hybrid"

    def test_returns_none_for_missing_workmode(self):
        assert normalize_workmode(None) is None
        assert normalize_workmode("") is None
