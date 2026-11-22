"""Exercise booking flows against the running images in the rollback release test."""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from secrets import token_hex, token_urlsafe
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit

import psycopg

from release_smoke import PageParser, test_http_client


CUSTOMER_BOOKING = "Releaseprov – kundens förfrågan"
ADMIN_BOOKING = "Releaseprov – administratörens förfrågan"
HISTORICAL_BOOKING = "Releaseprov – tidigare avbruten förfrågan"
NEW_BOOKING = "Releaseprov – ny förfrågan efter återställning"
CITY = "Göteborg"
VENUE = "Provvägen 12"


@dataclass
class Fixtures:
    email: str
    password: str
    start: str
    end: str
    customer_booking_id: int
    admin_booking_id: int
    historical_booking_id: int


class BookingPage(PageParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.statuses = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        classes = dict(attrs).get("class", "").split()
        if "status-pill" in classes:
            self.statuses.append(set(classes) - {"status-pill"})

    def handle_data(self, data):
        if data.strip():
            self.text_parts.append(data.strip())

    @property
    def booking_ids(self):
        return {
            int(link.rsplit("/", 1)[-1])
            for link in self.links
            if link and re.fullmatch(r"/bookings/[0-9]+", link)
        }


class Website:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.client = test_http_client(self.base_url)

    def page(self, path, *, fields=None, destination=None):
        data = urlencode(fields).encode("utf-8") if fields is not None else None
        with self.client.open(self.base_url + path, data=data, timeout=10) as response:
            expected_path = destination or path
            actual_path = urlsplit(response.url).path
            if actual_path != expected_path:
                raise RuntimeError(f"{path} ledde till {actual_path}. Förväntad sökväg var {expected_path}.")
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                raise RuntimeError(f"{path} svarade inte med en HTML-sida och HTTP 200.")
            page = BookingPage()
            page.feed(response.read().decode("utf-8"))
            return page

    def submit(self, page, path, destination, **fields):
        if not page.token:
            raise RuntimeError(f"Formuläret för {path} saknar CSRF-token.")
        return self.page(path, fields={**fields, "csrf_token": page.token}, destination=destination)

    def login(self, email, password, dashboard):
        return self.submit(self.page("/login"), "/login", dashboard, email=email, password=password)


def _create_booking(customer, course, start, end):
    before = customer.page("/dashboard").booking_ids
    dashboard = customer.submit(
        customer.page("/booking"), "/booking", "/dashboard",
        datumtyp="intervall", onskat_startdatum=start, onskat_slutdatum=end,
        utbildningskalla="fritext", egen_utbildning=course,
        ort=CITY, antal="4", lokalansvar="kund", lokaladress=VENUE,
    )
    new_ids = dashboard.booking_ids - before
    if len(new_ids) != 1:
        raise RuntimeError(f"Exakt en ny bokning ska visas efter att {course} skickats in.")
    return new_ids.pop()


def _check_booking(website, path, course, status, start, end):
    page = website.page(path)
    if not page.statuses or status not in page.statuses[0]:
        raise RuntimeError(f"{path} visar inte bokningsstatus {status}.")
    for value in (course, f"{start} – {end}", "4", CITY, VENUE):
        if value not in page.text_parts:
            raise RuntimeError(f"{path} saknar bokningsuppgiften {value}.")
    return page


def _check_both_views(customer, admin, booking_id, course, status, fixtures):
    path = f"/bookings/{booking_id}"
    _check_booking(customer, path, course, status, fixtures.start, fixtures.end)
    admin_page = _check_booking(admin, "/admin" + path, course, status, fixtures.start, fixtures.end)
    if fixtures.email not in admin_page.text_parts:
        raise RuntimeError(f"Administratörsvyn visar fel kund för bokning {booking_id}.")


def _check_original_bookings(base_url, admin_email, admin_password, fixtures):
    customer = Website(base_url)
    customer_dashboard = customer.login(fixtures.email, fixtures.password, "/dashboard")
    admin = Website(base_url)
    admin_dashboard = admin.login(admin_email, admin_password, "/admin/")

    for booking_id, course, status in (
        (fixtures.customer_booking_id, CUSTOMER_BOOKING, "pending"),
        (fixtures.admin_booking_id, ADMIN_BOOKING, "pending"),
        (fixtures.historical_booking_id, HISTORICAL_BOOKING, "cancelled"),
    ):
        path = f"/bookings/{booking_id}"
        if path not in customer_dashboard.links or "/admin" + path not in admin_dashboard.links:
            raise RuntimeError(f"Bokning {booking_id} saknas på kundens eller administratörens översikt.")
        _check_both_views(customer, admin, booking_id, course, status, fixtures)
    return customer, admin


def seed_bookings(base_url, admin_email, admin_password, verify_customer):
    """Create bookings using the previous image before any candidate migration."""
    customer = Website(base_url)
    email = f"releaseprov-{token_hex(6)}@example.com"
    password = token_urlsafe(24)
    customer.submit(
        customer.page("/signup"), "/signup", "/login",
        name="Releaseprovets testkund", email=email, password=password,
        organization_number="0123456789",
    )
    verify_customer(email)
    customer.login(email, password, "/dashboard")
    today = date.today()
    start = (today + timedelta(days=30)).isoformat()
    end = (today + timedelta(days=34)).isoformat()
    fixtures = Fixtures(
        email=email, password=password, start=start, end=end,
        customer_booking_id=_create_booking(customer, CUSTOMER_BOOKING, start, end),
        admin_booking_id=_create_booking(customer, ADMIN_BOOKING, start, end),
        historical_booking_id=_create_booking(customer, HISTORICAL_BOOKING, start, end),
    )
    historical_id = fixtures.historical_booking_id
    customer.submit(customer.page(f"/bookings/{historical_id}"), f"/cancel_booking/{historical_id}", "/dashboard")
    _check_original_bookings(base_url, admin_email, admin_password, fixtures)
    return fixtures


def verify_upgrade(base_url, admin_email, admin_password, fixtures):
    """Confirm the candidate can read bookings created by the previous image."""
    _check_original_bookings(base_url, admin_email, admin_password, fixtures)


def verify_rollback(base_url, admin_email, admin_password, fixtures):
    """Confirm the restored image can still read and write the upgraded database."""
    customer, admin = _check_original_bookings(base_url, admin_email, admin_password, fixtures)

    customer_id = fixtures.customer_booking_id
    admin_id = fixtures.admin_booking_id
    customer_detail = f"/bookings/{customer_id}"
    admin_detail = f"/admin/bookings/{admin_id}"
    try:
        customer.page(admin_detail)
    except HTTPError as error:
        error.close()
        if error.code != 403:
            raise
    else:
        raise RuntimeError("Kunden fick tillgång till administratörens bokningsvy.")

    customer.submit(customer.page(customer_detail), f"/cancel_booking/{customer_id}", "/dashboard")
    admin.submit(admin.page(admin_detail), admin_detail + "/status", admin_detail, target="registered")
    new_id = _create_booking(customer, NEW_BOOKING, fixtures.start, fixtures.end)

    for booking_id, course, status in (
        (customer_id, CUSTOMER_BOOKING, "cancelled"),
        (admin_id, ADMIN_BOOKING, "registered"),
        (fixtures.historical_booking_id, HISTORICAL_BOOKING, "cancelled"),
        (new_id, NEW_BOOKING, "pending"),
    ):
        _check_both_views(customer, admin, booking_id, course, status, fixtures)


def _verify_test_customer(email):
    # Mail delivery is disabled, so complete verification for the synthetic account.
    try:
        with psycopg.connect(os.environ["db_path"], connect_timeout=10) as connection:
            result = connection.execute(
                "UPDATE users SET email_verified = 1 WHERE email = %s AND role = 'user' AND email_verified = 0",
                (email,),
            )
            if result.rowcount != 1:
                raise RuntimeError("Releaseprovets väntande testkonto kunde inte verifieras.")
    except psycopg.Error as error:
        raise RuntimeError(f"Verifiering av testkontot misslyckades ({type(error).__name__}).") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "upgrade", "rollback"))
    args = parser.parse_args()
    base_url = "http://kursplan:8000"
    admin_email = os.environ["test_email"]
    admin_password = os.environ["test_password"]
    if args.phase == "seed":
        fixtures = seed_bookings(base_url, admin_email, admin_password, _verify_test_customer)
        print(json.dumps(asdict(fixtures)))
        return

    fixtures = Fixtures(**json.load(sys.stdin))
    check = verify_upgrade if args.phase == "upgrade" else verify_rollback
    check(base_url, admin_email, admin_password, fixtures)


if __name__ == "__main__":
    main()
