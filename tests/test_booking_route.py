from html.parser import HTMLParser

import database
import pytest
import admin.routes as admin_routes


def _details(db_path):
    rows = database.get_all_bookings(db_path)
    assert len(rows) == 1
    return database.get_booking_details_by_id(rows[0][0], db_path)


def _with_changes(data, **changes):
    result = data.copy()
    for key, value in changes.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


class _FormStateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))

    def input(self, name, *, value=None):
        matches = [item for item in self.inputs if item.get("name") == name]
        if value is not None:
            matches = [item for item in matches if item.get("value") == value]
        assert matches, f"Fältet {name!r} saknas i formuläret."
        return matches[0]


def test_booking_requires_login_for_get_and_post(client):
    get_response = client.get("/booking")
    post_response = client.post("/booking", data={"datumtyp": "exakt"})

    assert get_response.status_code == 302
    assert get_response.location.endswith("/login")
    assert post_response.status_code == 302
    assert post_response.location.endswith("/login")


def test_exact_catalog_request_is_persisted_through_route(
    logged_in_client, valid_booking_data, db_path
):
    response = logged_in_client.post("/booking", data=valid_booking_data)

    assert response.status_code == 302
    assert response.location.endswith("/dashboard")
    details = _details(db_path)
    assert details["datumtyp"] == "exakt"
    assert details["onskat_datum"] == "2027-03-18"
    assert details["onskat_startdatum"] is None
    assert details["onskat_slutdatum"] is None
    assert details["utbildning"] == "HLR"
    assert details["utbildningskalla"] == "katalog"
    assert details["antal"] == 3
    assert details["onskad_ort"] == "Göteborg"
    assert details["lokalansvar"] == "kund"
    assert details["lokaladress"] == "Testgatan 1"
    assert details["status"] == "pending"


def test_interval_custom_course_and_supplier_location_are_normalized(
    logged_in_client, valid_booking_data, db_path, project_root
):
    catalog_path = project_root / "utbildningar.json"
    catalog_before = catalog_path.read_bytes()
    data = _with_changes(
        valid_booking_data,
        datumtyp="intervall",
        onskat_datum="2035-01-01",
        onskat_startdatum="2027-05-10",
        onskat_slutdatum="2027-05-14",
        utbildningskalla="fritext",
        utbildning="HLR",
        egen_utbildning="  Ångpannekurs special  ",
        ort="   ",
        lokalansvar="sakerhetsutbildarna",
        lokaladress="Denna dolda adress får inte sparas",
    )

    response = logged_in_client.post("/booking", data=data)

    assert response.status_code == 302
    details = _details(db_path)
    assert details["datumtyp"] == "intervall"
    assert details["onskat_datum"] is None
    assert details["onskat_startdatum"] == "2027-05-10"
    assert details["onskat_slutdatum"] == "2027-05-14"
    assert details["utbildning"] == "Ångpannekurs special"
    assert details["utbildningskalla"] == "fritext"
    assert details["onskad_ort"] is None
    assert details["lokalansvar"] == "sakerhetsutbildarna"
    assert details["lokaladress"] is None
    assert catalog_path.read_bytes() == catalog_before


@pytest.mark.parametrize(
    ("changes", "error_fragment"),
    [
        ({"datumtyp": None}, "datum"),
        ({"onskat_datum": None}, "datum"),
        ({"onskat_datum": "inte-ett-datum"}, "datum"),
        (
            {
                "datumtyp": "intervall",
                "onskat_datum": "",
                "onskat_startdatum": None,
                "onskat_slutdatum": "2027-05-12",
            },
            "datum",
        ),
        (
            {
                "datumtyp": "intervall",
                "onskat_datum": "",
                "onskat_startdatum": "2027-05-12",
                "onskat_slutdatum": None,
            },
            "datum",
        ),
        (
            {
                "datumtyp": "intervall",
                "onskat_datum": "",
                "onskat_startdatum": "2027-05-12",
                "onskat_slutdatum": "2027-05-12",
            },
            "olika",
        ),
        (
            {
                "datumtyp": "intervall",
                "onskat_datum": "",
                "onskat_startdatum": "2027-05-13",
                "onskat_slutdatum": "2027-05-12",
            },
            "senare",
        ),
        ({"datumtyp": "okant"}, "datum"),
    ],
    ids=[
        "missing-mode",
        "missing-exact",
        "invalid-exact",
        "missing-start",
        "missing-end",
        "identical-range",
        "reversed-range",
        "unknown-mode",
    ],
)
def test_invalid_date_requests_are_rejected_without_insert(
    logged_in_client, valid_booking_data, db_path, changes, error_fragment
):
    response = logged_in_client.post(
        "/booking", data=_with_changes(valid_booking_data, **changes)
    )

    assert response.status_code == 400
    assert error_fragment in response.get_data(as_text=True).lower()
    assert database.get_all_bookings(db_path) == []


@pytest.mark.parametrize(
    ("changes", "error_fragment"),
    [
        ({"utbildningskalla": None}, "utbildning"),
        ({"utbildning": None}, "utbildning"),
        ({"utbildning": "En kurs som inte finns"}, "utbildning"),
        (
            {
                "utbildningskalla": "fritext",
                "utbildning": "",
                "egen_utbildning": "   ",
            },
            "namn",
        ),
        ({"utbildningskalla": "okand"}, "utbildning"),
    ],
    ids=[
        "missing-source",
        "missing-catalog-value",
        "unknown-catalog-value",
        "blank-custom-value",
        "unknown-source",
    ],
)
def test_invalid_course_requests_are_rejected_without_insert(
    logged_in_client, valid_booking_data, db_path, changes, error_fragment
):
    response = logged_in_client.post(
        "/booking", data=_with_changes(valid_booking_data, **changes)
    )

    assert response.status_code == 400
    assert error_fragment in response.get_data(as_text=True).lower()
    assert database.get_all_bookings(db_path) == []


@pytest.mark.parametrize(
    "antal",
    [None, "", "0", "-1", "1.5", "tre", pytest.param("9" * 5000, id="too-many-digits")],
)
def test_invalid_participant_counts_are_rejected_without_insert(
    logged_in_client, valid_booking_data, db_path, antal
):
    response = logged_in_client.post(
        "/booking", data=_with_changes(valid_booking_data, antal=antal)
    )

    assert response.status_code == 400
    assert "deltag" in response.get_data(as_text=True).lower()
    assert database.get_all_bookings(db_path) == []


@pytest.mark.parametrize(
    ("changes", "error_fragment"),
    [
        ({"lokalansvar": None}, "lokal"),
        ({"lokalansvar": "okant"}, "lokal"),
        ({"lokaladress": None}, "adress"),
        ({"lokaladress": "   "}, "adress"),
    ],
    ids=["missing-owner", "unknown-owner", "missing-address", "blank-address"],
)
def test_invalid_location_requests_are_rejected_without_insert(
    logged_in_client, valid_booking_data, db_path, changes, error_fragment
):
    response = logged_in_client.post(
        "/booking", data=_with_changes(valid_booking_data, **changes)
    )

    assert response.status_code == 400
    assert error_fragment in response.get_data(as_text=True).lower()
    assert database.get_all_bookings(db_path) == []


@pytest.mark.parametrize(
    ("ort", "expected"),
    [(None, None), ("   ", None), ("  Malmö  ", "Malmö")],
)
def test_optional_requested_city_is_normalized(
    logged_in_client, valid_booking_data, db_path, ort, expected
):
    response = logged_in_client.post(
        "/booking", data=_with_changes(valid_booking_data, ort=ort)
    )

    assert response.status_code == 302
    assert _details(db_path)["onskad_ort"] == expected


def test_missing_or_incorrect_csrf_token_is_rejected(
    logged_in_client, valid_booking_data, db_path
):
    missing = _with_changes(valid_booking_data, csrf_token=None)
    response = logged_in_client.post("/booking", data=missing)

    assert response.status_code == 400
    assert database.get_all_bookings(db_path) == []

    invalid = _with_changes(valid_booking_data, csrf_token="fel-token")
    response = logged_in_client.post("/booking", data=invalid)

    assert response.status_code == 400
    assert database.get_all_bookings(db_path) == []


def test_validation_error_preserves_values_and_modes(
    logged_in_client, valid_booking_data, db_path
):
    data = _with_changes(
        valid_booking_data,
        datumtyp="intervall",
        onskat_datum="",
        onskat_startdatum="2027-06-01",
        onskat_slutdatum="2027-06-05",
        utbildningskalla="fritext",
        utbildning="",
        egen_utbildning="Egen Å-kurs",
        ort="Örebro",
        antal="0",
        lokalansvar="kund",
        lokaladress="Ågatan 4",
    )

    response = logged_in_client.post("/booking", data=data)

    assert response.status_code == 400
    parser = _FormStateParser()
    parser.feed(response.get_data(as_text=True))
    assert "checked" in parser.input("datumtyp", value="intervall")
    assert parser.input("onskat_startdatum").get("value") == "2027-06-01"
    assert parser.input("onskat_slutdatum").get("value") == "2027-06-05"
    assert "checked" in parser.input("utbildningskalla", value="fritext")
    assert parser.input("egen_utbildning").get("value") == "Egen Å-kurs"
    assert parser.input("ort").get("value") == "Örebro"
    assert parser.input("antal").get("value") == "0"
    assert "checked" in parser.input("lokalansvar", value="kund")
    assert parser.input("lokaladress").get("value") == "Ågatan 4"
    assert database.get_all_bookings(db_path) == []


def test_customer_cannot_cancel_another_customers_or_an_accepted_booking(
    logged_in_client, valid_booking_data, db_path, csrf_from_html
):
    assert logged_in_client.post("/booking", data=valid_booking_data).status_code == 302
    own_booking_id = database.get_all_bookings(db_path)[0][0]
    database.book_bookings(
        [own_booking_id], "2027-04-01", "Göteborg", db_path
    )
    other_booking_id = database.create_booking_request(
        name="Other Customer",
        email="other-customer@example.com",
        organization_number="1122334455",
        utbildning="HLR",
        antal=2,
        datumtyp="exakt",
        onskat_datum="2027-04-15",
        utbildningskalla="katalog",
        onskad_ort=None,
        lokalansvar="sakerhetsutbildarna",
        db_path=db_path,
    )
    booking_form = logged_in_client.get("/booking")
    token = csrf_from_html(booking_form.get_data(as_text=True))

    other_response = logged_in_client.post(
        f"/cancel_booking/{other_booking_id}", data={"csrf_token": token}
    )
    refreshed_form = logged_in_client.get("/booking")
    refreshed_token = csrf_from_html(refreshed_form.get_data(as_text=True))
    accepted_response = logged_in_client.post(
        f"/cancel_booking/{own_booking_id}", data={"csrf_token": refreshed_token}
    )

    assert other_response.status_code == 302
    assert accepted_response.status_code == 302
    assert database.get_booking_details_by_id(other_booking_id, db_path)["status"] == "pending"
    assert database.get_booking_details_by_id(own_booking_id, db_path)["status"] == "accepted"


def test_admin_sends_confirmation_only_for_selected_pending_bookings(
    logged_in_client,
    valid_booking_data,
    db_path,
    csrf_from_html,
    monkeypatch,
):
    assert logged_in_client.post("/booking", data=valid_booking_data).status_code == 302
    pending_id = database.get_all_bookings(db_path)[0][0]
    already_accepted_id = database.create_booking_request(
        name="Already Accepted",
        email="accepted@example.com",
        organization_number="9988776655",
        utbildning="HLR",
        antal=2,
        datumtyp="exakt",
        onskat_datum="2027-04-15",
        utbildningskalla="katalog",
        onskad_ort="Malmö",
        lokalansvar="sakerhetsutbildarna",
        db_path=db_path,
    )
    database.book_bookings(
        [already_accepted_id], "2027-05-01", "Malmö", db_path
    )
    logged_in_client.get("/logout")
    database.create_user(
        "Mail Admin",
        "mail-admin@example.com",
        "mail-admin-password",
        "0000000000",
        "admin",
        db_path,
    )
    logged_in_client.post(
        "/login",
        data={"email": "mail-admin@example.com", "password": "mail-admin-password"},
    )
    dashboard = logged_in_client.get("/admin/")
    token = csrf_from_html(dashboard.get_data(as_text=True))
    sent_for = []

    def record_mail(email, booking_date, ort, booking_id, utbildning):
        sent_for.append(booking_id)
        return True

    monkeypatch.setattr(admin_routes.gen_func, "send_booked_mail", record_mail)
    response = logged_in_client.post(
        "/admin/bookings",
        data={
            "csrf_token": token,
            "booking_ids": [str(pending_id), str(already_accepted_id)],
            "date": "2027-06-01",
            "ort": "Uppsala",
        },
    )

    assert response.status_code == 302
    assert sent_for == [pending_id]
    assert database.get_booking_details_by_id(pending_id, db_path)["status"] == "accepted"
    accepted = database.get_booking_details_by_id(already_accepted_id, db_path)
    assert accepted["faststallt_datum"] == "2027-05-01"
    assert accepted["faststalld_ort"] == "Malmö"


def test_admin_confirmation_preserves_original_request(
    logged_in_client, valid_booking_data, db_path, csrf_from_html
):
    data = _with_changes(
        valid_booking_data,
        datumtyp="intervall",
        onskat_datum="",
        onskat_startdatum="2027-08-01",
        onskat_slutdatum="2027-08-10",
        ort="Västerås",
    )
    assert logged_in_client.post("/booking", data=data).status_code == 302
    booking_id = database.get_all_bookings(db_path)[0][0]

    logged_in_client.get("/logout")
    database.create_user(
        "Route Admin",
        "route-admin@example.com",
        "route-admin-password",
        "0000000000",
        "admin",
        db_path,
    )
    login = logged_in_client.post(
        "/login",
        data={
            "email": "route-admin@example.com",
            "password": "route-admin-password",
        },
    )
    assert login.status_code == 302
    admin_dashboard = logged_in_client.get("/admin/")
    assert admin_dashboard.status_code == 200
    admin_csrf_token = csrf_from_html(admin_dashboard.get_data(as_text=True))
    response = logged_in_client.post(
        "/admin/bookings",
        data={
            "csrf_token": admin_csrf_token,
            "booking_ids": str(booking_id),
            "date": "2027-09-15",
            "ort": "Uppsala",
        },
    )

    assert response.status_code == 302
    details = database.get_booking_details_by_id(booking_id, db_path)
    assert details["status"] == "accepted"
    assert details["faststallt_datum"] == "2027-09-15"
    assert details["faststalld_ort"] == "Uppsala"
    assert details["datumtyp"] == "intervall"
    assert details["onskat_datum"] is None
    assert details["onskat_startdatum"] == "2027-08-01"
    assert details["onskat_slutdatum"] == "2027-08-10"
    assert details["onskad_ort"] == "Västerås"
