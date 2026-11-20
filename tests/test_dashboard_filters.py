"""Customer status filters keep ownership and pending replies intact."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
import pytest

import database


EMAIL = "dashboard-filter-customer@example.test"
OTHER_EMAIL = "dashboard-filter-other@example.test"
OWN_STATUSES = tuple(status for status in database.BOOKING_STATUSES if status != "rescheduled")


class _FilterParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.booking_ids = []
        self.status_inputs = {}
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "article" and "booking-card" in attributes.get("class", "").split():
            self.booking_ids.append(int(attributes["aria-labelledby"].removeprefix("booking-title-")))
        elif tag == "input" and attributes.get("name") == "status":
            self.status_inputs[attributes["value"]] = attributes
        elif tag == "form":
            self.forms.append(attributes)


def _parse(response):
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parser = _FilterParser()
    parser.feed(html)
    return html, parser


@pytest.fixture
def filter_customer(scenario_app, all_db_path):
    user = database.create_user(
        "Filterkund", EMAIL, "dashboard-filter-password", "5566778899", "user", all_db_path,
    )
    bookings = {}
    for email, statuses in ((EMAIL, OWN_STATUSES), (OTHER_EMAIL, ("pending", "rescheduled"))):
        for status in statuses:
            booking_id = database.create_booking(
                "Filterkund", email, "5566778899", f"Utbildning {email} {status}",
                3, "Göteborg", "Testgatan 1", "2028-03-18", all_db_path,
            )
            # Seed each historical status without invoking unrelated workflow actions.
            with database._cursor(all_db_path) as cursor:
                database._execute(
                    cursor, "UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id),
                )
            if email == EMAIL:
                bookings[status] = booking_id
    client = scenario_app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = user[0]
    return client, bookings


def test_dashboard_without_filter_shows_all_own_bookings(filter_customer):
    client, bookings = filter_customer
    html, parser = _parse(client.get("/dashboard"))

    assert set(parser.booking_ids) == set(bookings.values())
    assert set(parser.status_inputs) == set(database.BOOKING_STATUSES)
    assert all(attributes["type"] == "checkbox" for attributes in parser.status_inputs.values())
    assert all("checked" not in attributes for attributes in parser.status_inputs.values())
    assert any(form.get("method", "").lower() == "get" and form.get("action") == "/dashboard"
               for form in parser.forms)
    assert OTHER_EMAIL not in html


@pytest.mark.parametrize("status", database.BOOKING_STATUSES)
def test_each_status_filters_only_the_customers_matching_bookings(filter_customer, status):
    client, bookings = filter_customer
    html, parser = _parse(client.get("/dashboard", query_string={"status": status}))

    expected = [bookings[status]] if status in bookings else []
    assert parser.booking_ids == expected
    assert {value for value, attrs in parser.status_inputs.items() if "checked" in attrs} == {status}
    assert f"Visar {len(expected)} av {len(bookings)} bokningar" in html
    assert OTHER_EMAIL not in html


def test_multiple_statuses_keep_selected_values_and_database_unchanged(filter_customer, all_db_path):
    client, bookings = filter_customer
    before = database.list_booking_details(all_db_path)
    html, parser = _parse(client.get(
        "/dashboard", query_string=[("status", "pending"), ("status", "accepted")],
    ))

    assert set(parser.booking_ids) == {bookings["pending"], bookings["accepted"]}
    assert {value for value, attrs in parser.status_inputs.items() if "checked" in attrs} == {
        "pending", "accepted",
    }
    assert "1 datumförslag väntar på ditt svar." in html
    assert f"Visar 2 av {len(bookings)} bokningar" in html
    assert database.list_booking_details(all_db_path) == before


@pytest.mark.parametrize("values, expected_statuses", [
    (["unknown"], set()),
    (["", "UNKNOWN", "<script>alert(1)</script>"], set()),
    (["accepted", "unknown", "accepted"], {"accepted"}),
    (["pending", "pending", "cancelled"], {"pending", "cancelled"}),
])
def test_invalid_and_repeated_filter_values_are_handled(filter_customer, values, expected_statuses):
    client, bookings = filter_customer
    html, parser = _parse(client.get(
        "/dashboard", query_string=[("status", value) for value in values],
    ))

    expected_ids = {bookings[status] for status in expected_statuses} if expected_statuses else set(bookings.values())
    assert set(parser.booking_ids) == expected_ids
    assert {value for value, attrs in parser.status_inputs.items() if "checked" in attrs} == expected_statuses
    assert "<script>alert(1)</script>" not in html
    assert OTHER_EMAIL not in html


def test_empty_filter_result_keeps_controls_and_reply_reminder(filter_customer):
    client, bookings = filter_customer
    html, parser = _parse(client.get("/dashboard?status=rescheduled"))

    assert parser.booking_ids == []
    assert "Inga bokningar matchar dina val." in html
    assert "Du har inga bokningar ännu." not in html
    assert "1 datumförslag väntar på ditt svar." in html
    assert f"Visar 0 av {len(bookings)} bokningar" in html
    assert set(parser.status_inputs) == set(database.BOOKING_STATUSES)
    assert 'href="/dashboard"' in html


def test_filters_require_login(scenario_app):
    response = scenario_app.test_client().get("/dashboard?status=accepted")

    assert response.status_code == 302
    assert response.location.endswith("/login")


@pytest.mark.parametrize("width", [320, 1280])
def test_filter_form_submits_multiple_statuses_and_can_reset(page, filter_customer, width):
    client, bookings = filter_customer

    def serve_local(route):
        url = urlsplit(route.request.url)
        response = client.get(url.path + (f"?{url.query}" if url.query else ""))
        route.fulfill(status=response.status_code, body=response.get_data(), content_type=response.content_type)

    page.route("http://dashboard-filters.test/**", serve_local)
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("http://dashboard-filters.test/dashboard")
    filters = page.get_by_role("group", name="Filtrera på status")
    expect(filters).to_be_visible()
    pending = filters.get_by_role("checkbox", name="Ny förfrågan", exact=True)
    accepted = filters.get_by_role("checkbox", name="Accepterad", exact=True)
    pending.focus()
    page.keyboard.press("Space")
    expect(pending).to_be_checked()
    accepted.check()
    page.get_by_role("button", name="Filtrera", exact=True).click()

    assert set(parse_qs(urlsplit(page.url).query)["status"]) == {"pending", "accepted"}
    expect(page.get_by_role("article")).to_have_count(2)
    expect(page.get_by_role("checkbox", name="Ny förfrågan", exact=True)).to_be_checked()
    expect(page.get_by_role("checkbox", name="Accepterad", exact=True)).to_be_checked()
    expect(page.get_by_text("1 datumförslag väntar på ditt svar.", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.get_by_role("link", name="Visa alla", exact=True).click()

    assert not urlsplit(page.url).query
    expect(page.get_by_role("article")).to_have_count(len(bookings))
    expect(page.locator('input[name="status"]:checked')).to_have_count(0)
