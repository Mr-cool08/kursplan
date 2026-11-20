"""Read company names from mackan.eu's free Bolagsverket JSON API."""

from collections import OrderedDict
from dataclasses import dataclass
from http.client import HTTPException
import json
import logging
import re
from threading import Lock
from time import monotonic
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_URL = "https://mackan.eu/tools/bolagsverket/get_data.php"
REQUEST_TIMEOUT = 3
MAX_RESPONSE_BYTES = 256 * 1024
CACHE_TTL = 24 * 60 * 60
NOT_FOUND_TTL = 60 * 60
ERROR_TTL = 60
MAX_CACHE_ENTRIES = 256
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LookupResult:
    status: str
    name: str | None = None


def normalize_organization_number(raw):
    """Accept legal entities only; never forward a sole trader's personal number."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not re.fullmatch(r"[0-9]{6}-?[0-9]{4}", value):
        return None
    value = value.replace("-", "")
    if value[0] == "0" or int(value[2]) < 2:
        return None
    digits = [int(character) for character in value]
    total = sum(sum(divmod(number * (2 if index % 2 == 0 else 1), 10))
                for index, number in enumerate(digits))
    return value if total % 10 == 0 else None


def _extract_company_name(payload, expected_org_number):
    """Use the registered name, never an alias or a different organisation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("organisationer"), list):
        raise ValueError("Invalid company response")
    for organisation in payload["organisationer"]:
        if not isinstance(organisation, dict):
            raise ValueError("Invalid organisation")
        names = organisation.get("organisationsnamn")
        identity = organisation.get("organisationsidentitet")
        if not isinstance(names, dict):
            raise ValueError("Missing organisation names")
        error = names.get("fel")
        if identity is None and isinstance(error, dict) and error.get("typ") == "ORGANISATION_FINNS_EJ":
            continue
        if not isinstance(identity, dict) or identity.get("identitetsbeteckning") != expected_org_number:
            raise ValueError("Unexpected organisation identity")
        name_list = names.get("organisationsnamnLista")
        if not isinstance(name_list, list) or error:
            raise ValueError("Invalid organisation names")
        for entry in name_list:
            if not isinstance(entry, dict) or not isinstance(entry.get("organisationsnamntyp"), dict):
                raise ValueError("Invalid organisation name")
            if entry["organisationsnamntyp"].get("kod") != "FORETAGSNAMN":
                continue
            name = entry.get("namn")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 500:
                raise ValueError("Invalid company name")
            if any(ord(character) < 32 for character in name):
                raise ValueError("Invalid company name characters")
            return name.strip()
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The external destination is fixed, including when the provider fails.
        return None


def _fetch_company_name(org_number):
    request = Request(
        f"{API_URL}?orgnr={org_number}",
        headers={"Accept": "application/json", "User-Agent": "Kursplan/1.0 (company lookup)"},
    )
    with build_opener(_NoRedirect()).open(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Company response too large")
    return _extract_company_name(json.loads(body), org_number)


class CompanyLookup:
    """Bounded process-local cache; serialize misses and pause after API errors."""

    def __init__(self):
        self._cache = OrderedDict()
        self._lock = Lock()
        self._retry_after = 0

    def lookup(self, raw_number):
        org_number = normalize_organization_number(raw_number)
        if org_number is None:
            return LookupResult("invalid")
        # Keep a single request in flight and coalesce concurrent duplicate lookups.
        with self._lock:
            now = monotonic()
            cached = self._cache.get(org_number)
            if cached is not None:
                expires, result = cached
                if expires > now:
                    self._cache.move_to_end(org_number)
                    return result
                del self._cache[org_number]
            if now < self._retry_after:
                return LookupResult("unavailable")
            try:
                name = _fetch_company_name(org_number)
            except (URLError, OSError, HTTPException, ValueError):
                # Exception text can contain organisation data or response contents.
                logger.warning("Företagsuppslagning misslyckades; nya API-anrop pausas tillfälligt.")
                self._retry_after = monotonic() + ERROR_TTL
                return LookupResult("unavailable")
            result = LookupResult("found", name) if name else LookupResult("not_found")
            ttl = CACHE_TTL if name else NOT_FOUND_TTL
            self._cache[org_number] = (monotonic() + ttl, result)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return result


service = CompanyLookup()
