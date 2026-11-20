"""Boundary values and customer actions omitted from the happy-path workflow."""

from urllib.parse import parse_qs, urlsplit

from markupsafe import escape
import pytest

import database
from users.routes import _validate_booking_form


EMAIL = "booking-scenarios@example.test"
PASSWORD = "booking-scenarios-password"
VALID_FORM = {
    "datumtyp": "exakt",
    "onskat_datum": "2028-02-29",
    "onskat_startdatum": "",
    "onskat_slutdatum": "",
    "utbildningskalla": "katalog",
    "utbildning": "HLR",
    "egen_utbildning": "",
    "antal": "1",
    "ort": "Malmö",
    "lokalansvar": "kund",
    "lokaladress": "Ågatan 1",
}


@pytest.mark.parametrize("value", [
    "2027-02-29", "2028-02-30", "2028-13-01", "2028-00-01",
    "2028-01-00", "2028-04-31", "20280229", "2028-W09-2",
    "2028-2-29", "2028-02-29T00:00:00", "0000-01-01",
])
def test_booking_rejects_impossible_and_noncanonical_calendar_dates(value):
    normalized, errors = _validate_booking_form(VALID_FORM | {"onskat_datum": value}, ["HLR"])

    assert "onskat_datum" in errors
    assert normalized["onskat_datum"] is None


@pytest.mark.parametrize("field", ["onskat_startdatum", "onskat_slutdatum"])
@pytest.mark.parametrize("value", ["2028-02-30", "20280229", "2028-W09-2"])
def test_interval_validates_each_date_even_when_other_endpoint_is_valid(field, value):
    data = VALID_FORM | {
        "datumtyp": "intervall", "onskat_startdatum": "2028-02-28",
        "onskat_slutdatum": "2028-03-01", field: value,
    }

    normalized, errors = _validate_booking_form(data, ["HLR"])

    assert field in errors
    assert normalized["datumtyp"] is None
    assert normalized["onskat_startdatum"] is None
    assert normalized["onskat_slutdatum"] is None


@pytest.mark.parametrize("value", ["2147483648", "1e3", "+1", "1_000", "1,0", "١", "１"])
def test_participants_reject_overflow_and_ambiguous_number_formats(value):
    normalized, errors = _validate_booking_form(VALID_FORM | {"antal": value}, ["HLR"])

    assert "antal" in errors
    assert normalized["antal"] is None


@pytest.mark.parametrize(("value", "expected"), [("1", 1), (" 0007 ", 7), ("2147483647", 2147483647)])
def test_participant_count_accepts_integer_boundaries(value, expected):
    normalized, errors = _validate_booking_form(VALID_FORM | {"antal": value}, ["HLR"])

    assert errors == {}
    assert normalized["antal"] == expected


def test_booking_collects_independent_errors_in_one_response():
    normalized, errors = _validate_booking_form({}, ["HLR"])

    assert set(errors) == {"datumtyp", "utbildningskalla", "antal", "lokalansvar"}
    assert all(value is None for value in normalized.values())


def test_normalization_ignores_inactive_fields_and_trims_active_values():
    normalized, errors = _validate_booking_form(VALID_FORM | {
        "datumtyp": " exakt ", "onskat_datum": " 2028-02-29 ",
        "onskat_startdatum": "invalid hidden date", "onskat_slutdatum": "invalid hidden date",
        "utbildningskalla": " katalog ", "utbildning": " HLR ",
        "egen_utbildning": "ignored custom course", "ort": " Malmö ",
        "lokalansvar": " kund ", "lokaladress": " Ågatan 1 ",
    }, ["HLR"])

    assert errors == {}
    assert normalized == {
        "datumtyp": "exakt", "onskat_datum": "2028-02-29",
        "onskat_startdatum": None, "onskat_slutdatum": None,
        "utbildning": "HLR", "utbildningskalla": "katalog", "antal": 1,
        "onskad_ort": "Malmö", "lokalansvar": "kund", "lokaladress": "Ågatan 1",
    }


@pytest.fixture
def customer_client(scenario_app, all_db_path, csrf_from_html):
    database.create_user("Bokningskund Åsa", EMAIL, PASSWORD, "5566778899", "user", all_db_path)
    client = scenario_app.test_client()
    token = csrf_from_html(client.get("/login").get_data(as_text=True))
    response = client.post("/login", data={"csrf_token": token, "email": EMAIL, "password": PASSWORD})
    assert response.status_code == 302
    return client


def _token(client, csrf_from_html):
    return csrf_from_html(client.get("/booking").get_data(as_text=True))


def _create_booking(path, *, email=EMAIL, course="HLR"):
    return database.create_booking_request(
        name="Bokningskund Åsa", email=email, organization_number="5566778899",
        utbildning=course, antal=1, datumtyp="exakt", onskat_datum="2028-02-29",
        utbildningskalla="fritext", onskad_ort="Malmö", lokalansvar="kund",
        lokaladress="Ågatan 1", db_path=path,
    )


def test_successful_booking_consumes_token_and_replay_cannot_duplicate_request(
    customer_client, all_db_path, csrf_from_html,
):
    token = _token(customer_client, csrf_from_html)
    data = VALID_FORM | {"csrf_token": token}

    assert customer_client.post("/booking", data=data).status_code == 302
    assert customer_client.post("/booking", data=data).status_code == 400
    assert len(database.list_booking_details(all_db_path, email=EMAIL)) == 1
    fresh = _token(customer_client, csrf_from_html)
    assert fresh != token
    assert customer_client.post("/booking", data=data | {"csrf_token": fresh}).status_code == 302
    assert len(database.list_booking_details(all_db_path, email=EMAIL)) == 2


def test_validation_failure_can_be_corrected_using_same_form_token(
    customer_client, all_db_path, csrf_from_html,
):
    token = _token(customer_client, csrf_from_html)
    invalid = VALID_FORM | {"csrf_token": token, "antal": "0"}
    response = customer_client.post("/booking", data=invalid)
    assert response.status_code == 400
    assert csrf_from_html(response.get_data(as_text=True)) == token
    assert database.list_booking_details(all_db_path) == []

    assert customer_client.post("/booking", data=invalid | {"antal": "1"}).status_code == 302
    assert len(database.list_booking_details(all_db_path)) == 1


def test_form_cannot_override_booking_owner_or_status(customer_client, all_db_path, csrf_from_html):
    response = customer_client.post("/booking", data=VALID_FORM | {
        "csrf_token": _token(customer_client, csrf_from_html),
        "name": "Falsk ägare", "email": "other@example.test",
        "organization_number": "0000000000", "status": "accepted", "role": "admin",
        "faststallt_datum": "2030-01-01", "faststalld_ort": "Annan ort",
    })

    assert response.status_code == 302
    booking, = database.list_booking_details(all_db_path)
    assert (booking["name"], booking["email"], booking["organization_number"]) == (
        "Bokningskund Åsa", EMAIL, "5566778899",
    )
    assert booking["status"] == "pending"
    assert booking["faststallt_datum"] is None
    assert booking["faststalld_ort"] is None


@pytest.mark.parametrize("endpoint", ["/bookings/987654321", "/bookings/987654321/proposals/1/respond"])
def test_unknown_booking_returns_404_without_creating_data(
    customer_client, all_db_path, csrf_from_html, endpoint,
):
    if endpoint.endswith("/respond"):
        response = customer_client.post(endpoint, data={
            "csrf_token": _token(customer_client, csrf_from_html), "action": "accept",
        })
    else:
        response = customer_client.get(endpoint)

    assert response.status_code == 404
    assert database.list_booking_details(all_db_path) == []


def test_unknown_cancellation_returns_generic_failure_without_history(
    customer_client, all_db_path, csrf_from_html,
):
    response = customer_client.post("/cancel_booking/987654321", data={
        "csrf_token": _token(customer_client, csrf_from_html),
    }, follow_redirects=True)

    assert response.status_code == 200
    assert "Bokningen kunde inte avbrytas" in response.get_data(as_text=True)
    assert database.list_booking_details(all_db_path) == []


@pytest.mark.parametrize("action", ["accept", "new_date", "cancel"])
def test_proposal_from_another_booking_cannot_change_either_booking(
    customer_client, all_db_path, csrf_from_html, action,
):
    first = _create_booking(all_db_path)
    second = _create_booking(all_db_path)
    first_proposal = database.create_proposal(first, "2028-03-01", "Malmö", "Sal 1", all_db_path)
    second_proposal = database.create_proposal(second, "2028-03-02", "Lund", "Sal 2", all_db_path)
    histories = {bid: database.get_booking_history(bid, all_db_path) for bid in (first, second)}

    response = customer_client.post(f"/bookings/{first}/proposals/{second_proposal['id']}/respond", data={
        "csrf_token": _token(customer_client, csrf_from_html), "action": action,
    })

    assert response.status_code == 409
    for bid in (first, second):
        assert database.get_booking_details_by_id(bid, all_db_path)["status"] == "awaiting_response"
        assert database.get_booking_history(bid, all_db_path) == histories[bid]
        assert database.get_booking_proposals(bid, all_db_path)[0]["state"] == "pending"
    assert database.get_booking_proposals(first, all_db_path)[0]["id"] == first_proposal["id"]


@pytest.mark.parametrize(("action", "expected_status", "expected_proposal"), [
    ("accept", "accepted", "accepted"),
    ("new_date", "new_date_requested", "declined"),
    ("cancel", "cancelled", "cancelled"),
])
def test_each_proposal_response_has_one_transition_and_cannot_be_replayed(
    customer_client, all_db_path, csrf_from_html, action, expected_status, expected_proposal,
):
    bid = _create_booking(all_db_path)
    proposal = database.create_proposal(bid, "2028-03-01", "Lund", "Sal 1", all_db_path)
    endpoint = f"/bookings/{bid}/proposals/{proposal['id']}/respond"
    token = _token(customer_client, csrf_from_html)
    response = customer_client.post(endpoint, data={"csrf_token": token, "action": action})

    assert response.status_code == 302
    history = database.get_booking_history(bid, all_db_path)
    assert history[-1]["actor"] == "customer"
    assert history[-1]["to_status"] == expected_status
    assert database.get_booking_details_by_id(bid, all_db_path)["status"] == expected_status
    assert database.get_booking_proposals(bid, all_db_path)[0]["state"] == expected_proposal
    assert customer_client.post(endpoint, data={"csrf_token": token, "action": action}).status_code == 400
    assert customer_client.post(endpoint, data={
        "csrf_token": _token(customer_client, csrf_from_html), "action": action,
    }).status_code == 409
    assert database.get_booking_history(bid, all_db_path) == history


def test_anonymous_response_redirects_to_login_without_answering_proposal(
    scenario_app, customer_client, all_db_path,
):
    bid = _create_booking(all_db_path)
    proposal = database.create_proposal(bid, "2028-03-01", "Lund", "Sal 1", all_db_path)
    response = scenario_app.test_client().post(
        f"/bookings/{bid}/proposals/{proposal['id']}/respond", data={"action": "accept"},
    )

    assert response.status_code == 302
    target = urlsplit(response.location)
    assert target.path == "/login"
    assert parse_qs(target.query)["next"] == [f"/bookings/{bid}"]
    assert database.get_booking_details_by_id(bid, all_db_path)["status"] == "awaiting_response"
    assert database.get_booking_proposals(bid, all_db_path)[0]["response"] is None


@pytest.mark.parametrize("operation", ["booking", "cancel", "respond"])
def test_token_from_a_different_browser_session_cannot_change_customer_data(
    scenario_app, customer_client, all_db_path, csrf_from_html, operation,
):
    bid = _create_booking(all_db_path)
    proposal = database.create_proposal(bid, "2028-03-01", "Lund", "Sal 1", all_db_path)
    _token(customer_client, csrf_from_html)
    foreign_token = csrf_from_html(scenario_app.test_client().get("/login").get_data(as_text=True))
    before = database.get_booking_history(bid, all_db_path)
    if operation == "booking":
        endpoint, data = "/booking", VALID_FORM
    elif operation == "cancel":
        endpoint, data = f"/cancel_booking/{bid}", {}
    else:
        endpoint, data = f"/bookings/{bid}/proposals/{proposal['id']}/respond", {"action": "accept"}

    response = customer_client.post(endpoint, data=data | {"csrf_token": foreign_token})

    assert response.status_code == 400
    assert len(database.list_booking_details(all_db_path)) == 1
    assert database.get_booking_details_by_id(bid, all_db_path)["status"] == "awaiting_response"
    assert database.get_booking_proposals(bid, all_db_path)[0]["response"] is None
    assert database.get_booking_history(bid, all_db_path) == before


@pytest.mark.parametrize("operation", ["cancel", "respond"])
def test_get_requests_cannot_cancel_or_answer_bookings(customer_client, all_db_path, operation):
    bid = _create_booking(all_db_path)
    proposal = database.create_proposal(bid, "2028-03-01", "Lund", "Sal 1", all_db_path)
    before = database.get_booking_history(bid, all_db_path)
    endpoint = (f"/cancel_booking/{bid}" if operation == "cancel" else
                f"/bookings/{bid}/proposals/{proposal['id']}/respond?action=accept")

    assert customer_client.get(endpoint).status_code == 405
    assert database.get_booking_details_by_id(bid, all_db_path)["status"] == "awaiting_response"
    assert database.get_booking_proposals(bid, all_db_path)[0]["response"] is None
    assert database.get_booking_history(bid, all_db_path) == before


def test_custom_course_and_location_are_escaped_in_form_overview_and_details(
    customer_client, all_db_path, csrf_from_html,
):
    course = '<img src=x onerror="alert(1)"> Säkerhet & miljö'
    address = '<script>alert("adress")</script> Ågatan & torget'
    data = VALID_FORM | {
        "csrf_token": _token(customer_client, csrf_from_html),
        "utbildningskalla": "fritext", "egen_utbildning": course,
        "lokaladress": address, "ort": '<b>Göteborg</b>', "antal": "0",
    }
    invalid = customer_client.post("/booking", data=data)
    assert invalid.status_code == 400
    assert escape(course) in invalid.get_data(as_text=True)
    assert escape(address) in invalid.get_data(as_text=True)
    assert customer_client.post("/booking", data=data | {"antal": "1"}).status_code == 302
    booking, = database.list_booking_details(all_db_path)
    assert booking["utbildning"] == course and booking["lokaladress"] == address

    for url in ("/dashboard", f"/bookings/{booking['id']}"):
        response = customer_client.get(url)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert escape(course) in html
        assert escape(address) in html
        assert '<img src=x' not in html
        assert '<script>alert' not in html

