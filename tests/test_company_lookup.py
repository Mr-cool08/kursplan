"""Company lookup contracts without requests to the external provider."""
import json
from unittest.mock import MagicMock, Mock
from urllib.error import URLError

import pytest

import company_lookup


ORG_NUMBER = "5560747551"


def provider_payload(number=ORG_NUMBER, name="Ångström & Partners AB"):
    return {"organisationer": [{
        "organisationsidentitet": {"identitetsbeteckning": number},
        "organisationsnamn": {"fel": None, "organisationsnamnLista": [
            {"namn": "Ett särskilt företagsnamn", "organisationsnamntyp": {"kod": "SARS_FORNAMN"}},
            {"namn": name, "organisationsnamntyp": {"kod": "FORETAGSNAMN"}},
        ]},
    }]}


@pytest.mark.parametrize("number", [ORG_NUMBER, "556074-7551", " 556074-7551 "])
def test_normalizes_valid_organization_number(number):
    assert company_lookup.normalize_organization_number(number) == ORG_NUMBER


@pytest.mark.parametrize("number", [
    None, "", "5560747552", "556074755", "55607475511", "165560747551",
    "19900101-0017", "9001010017", "556074+7551", "556074 7551",
    "５５６０７４７５５１", "https://example.invalid/5560747551",
])
def test_invalid_numbers_and_personal_identity_numbers_never_trigger_lookup(monkeypatch, number):
    fetch = Mock(side_effect=AssertionError("No external request expected"))
    monkeypatch.setattr(company_lookup, "_fetch_company_name", fetch)
    result = company_lookup.CompanyLookup().lookup(number)
    assert result == company_lookup.LookupResult("invalid")
    fetch.assert_not_called()


def test_selects_registered_name_for_exact_organization():
    assert company_lookup._extract_company_name(provider_payload(), ORG_NUMBER) == "Ångström & Partners AB"


def test_missing_organization_in_success_response_is_not_found():
    payload = {"organisationer": [{
        "organisationsidentitet": None,
        "organisationsnamn": {"fel": {"typ": "ORGANISATION_FINNS_EJ"}, "organisationsnamnLista": []},
    }]}
    assert company_lookup._extract_company_name(payload, ORG_NUMBER) is None
    assert company_lookup._extract_company_name({"organisationer": []}, ORG_NUMBER) is None


@pytest.mark.parametrize("payload", [
    None, [], {}, {"organisationer": "unexpected"}, {"organisationer": [None]},
    provider_payload(number="5560360793"),
    provider_payload(name=""),
    provider_payload(name="X" * 501),
])
def test_malformed_or_mismatched_response_cannot_supply_company_name(payload):
    with pytest.raises(ValueError):
        company_lookup._extract_company_name(payload, ORG_NUMBER)


def test_success_cache_uses_normalized_number_and_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(company_lookup, "monotonic", lambda: clock[0])
    fetch = Mock(side_effect=["Första namnet AB", "Nytt namn AB"])
    monkeypatch.setattr(company_lookup, "_fetch_company_name", fetch)
    lookup = company_lookup.CompanyLookup()

    assert lookup.lookup("556074-7551") == company_lookup.LookupResult("found", "Första namnet AB")
    clock[0] += company_lookup.CACHE_TTL - 1
    assert lookup.lookup(ORG_NUMBER).name == "Första namnet AB"
    assert fetch.call_count == 1
    clock[0] += 2
    assert lookup.lookup(ORG_NUMBER).name == "Nytt namn AB"
    assert fetch.call_count == 2


def test_not_found_is_cached_for_shorter_period(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(company_lookup, "monotonic", lambda: clock[0])
    fetch = Mock(return_value=None)
    monkeypatch.setattr(company_lookup, "_fetch_company_name", fetch)
    lookup = company_lookup.CompanyLookup()
    assert lookup.lookup(ORG_NUMBER).status == "not_found"
    assert lookup.lookup(ORG_NUMBER).status == "not_found"
    assert fetch.call_count == 1
    clock[0] += company_lookup.NOT_FOUND_TTL + 1
    assert lookup.lookup(ORG_NUMBER).status == "not_found"
    assert fetch.call_count == 2


@pytest.mark.parametrize("error", [URLError("private provider response"), TimeoutError(), ValueError("invalid JSON")])
def test_upstream_failure_pauses_requests_without_leaking_exception(monkeypatch, caplog, error):
    clock = [100.0]
    monkeypatch.setattr(company_lookup, "monotonic", lambda: clock[0])
    fetch = Mock(side_effect=[error, "Återhämtat AB"])
    monkeypatch.setattr(company_lookup, "_fetch_company_name", fetch)
    lookup = company_lookup.CompanyLookup()
    assert lookup.lookup(ORG_NUMBER) == company_lookup.LookupResult("unavailable")
    assert lookup.lookup("5560360793").status == "unavailable"
    assert fetch.call_count == 1
    assert "private provider response" not in caplog.text
    clock[0] += company_lookup.ERROR_TTL + 1
    assert lookup.lookup(ORG_NUMBER).name == "Återhämtat AB"


def test_cache_has_bounded_size(monkeypatch):
    monkeypatch.setattr(company_lookup, "MAX_CACHE_ENTRIES", 2)
    fetch = Mock(return_value="Exempel AB")
    monkeypatch.setattr(company_lookup, "_fetch_company_name", fetch)
    lookup = company_lookup.CompanyLookup()
    for number in (ORG_NUMBER, "5560360793", "5599999991"):
        assert lookup.lookup(number).status == "found"
    assert fetch.call_count == 3
    assert lookup.lookup(ORG_NUMBER).status == "found"
    assert fetch.call_count == 4


def test_http_request_uses_fixed_provider_timeout_and_bounded_response(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(provider_payload()).encode("utf-8")
    opener = Mock()
    opener.open.return_value = response
    build = Mock(return_value=opener)
    monkeypatch.setattr(company_lookup, "build_opener", build)

    assert company_lookup._fetch_company_name(ORG_NUMBER) == "Ångström & Partners AB"
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://mackan.eu/tools/bolagsverket/get_data.php?orgnr=5560747551"
    assert opener.open.call_args.kwargs["timeout"] == company_lookup.REQUEST_TIMEOUT
    assert request.get_header("Accept") == "application/json"
    response.read.assert_called_once_with(company_lookup.MAX_RESPONSE_BYTES + 1)
    handler = build.call_args.args[0]
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://example.invalid/") is None


@pytest.mark.parametrize("body", [b"not JSON", b"X" * (256 * 1024 + 1)])
def test_invalid_or_oversized_http_response_is_rejected(monkeypatch, body):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = body
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(company_lookup, "build_opener", Mock(return_value=opener))
    with pytest.raises(ValueError):
        company_lookup._fetch_company_name(ORG_NUMBER)
