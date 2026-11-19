import sqlite3

import psycopg
import pytest

import database


EMAIL = "test@example.com"
PASSWORD = "test-password"
ORGANIZATION = "0123456789"
NAME = "Åsa Öberg"
DATE = "2026-12-01"


def add_booking(db_path, *, name=NAME, email=EMAIL, course="Första hjälpen"):
    return database.create_booking(
        name, email, ORGANIZATION, course, 2, "Göteborg", "Sal 2", DATE, db_path
    )


def test_generate_secret_key():
    key = database.generate_secret_key()

    assert len(key) == 64
    assert len(bytes.fromhex(key)) == 32
    assert key != database.generate_secret_key()


def test_create_database_is_idempotent(db_path):
    booking_id = add_booking(db_path)
    user = database.create_user(NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path)

    database.create_database(db_path)

    assert [row[0] for row in database.get_all_bookings(db_path)] == [booking_id]
    assert database.login_user(EMAIL, PASSWORD, "user", db_path) == user[:3]


def test_create_user_persists_details(db_path):
    assert database.check_user_exists(EMAIL, db_path) is False

    user_id, name, organization, email = database.create_user(
        NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path
    )

    assert user_id > 0
    assert (name, organization, email) == (NAME, ORGANIZATION, EMAIL)
    assert database.check_user_exists(EMAIL, db_path) is True
    assert database.login_user(EMAIL, PASSWORD, "user", db_path) == (
        user_id, NAME, ORGANIZATION
    )
    assert database.authenticate_user(EMAIL, PASSWORD, db_path) == (
        user_id, NAME, ORGANIZATION, EMAIL, "user"
    )
    assert database.get_user_role(user_id, db_path) == "user"


def test_authentication_and_role_lookup_follow_external_role_change(db_path):
    user_id = database.create_user(
        NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path
    )[0]

    with psycopg.connect(db_path) as connection:
        connection.execute(
            "UPDATE users SET role = %s WHERE email = %s", ("admin", EMAIL)
        )

    assert database.authenticate_user(EMAIL, PASSWORD, db_path) == (
        user_id, NAME, ORGANIZATION, EMAIL, "admin"
    )
    assert database.get_user_role(user_id, db_path) == "admin"


def test_login_rejects_incorrect_email_password_or_role(db_path):
    database.create_user(NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path)

    assert database.login_user("other@example.com", PASSWORD, "user", db_path) is None
    assert database.login_user(EMAIL, "wrong-password", "user", db_path) is None
    assert database.login_user(EMAIL, PASSWORD, "admin", db_path) is None
    assert database.authenticate_user("other@example.com", PASSWORD, db_path) is None
    assert database.authenticate_user(EMAIL, "wrong-password", db_path) is None
    assert database.get_user_role(999999, db_path) is None


def test_create_user_rejects_duplicate_email(db_path):
    original = database.create_user(NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path)

    with pytest.raises(psycopg.errors.UniqueViolation):
        database.create_user("Another user", EMAIL, "replacement", ORGANIZATION, "admin", db_path)

    assert database.login_user(EMAIL, PASSWORD, "user", db_path) == original[:3]
    assert database.login_user(EMAIL, "replacement", "admin", db_path) is None


def test_ensure_test_user_creates_admin_without_replacing_password(db_path):
    database.ensure_test_user(EMAIL, PASSWORD, db_path)
    admin = database.login_user(EMAIL, PASSWORD, "admin", db_path)

    assert admin is not None
    assert admin[1:] == ("Admin", "0000000000")

    database.ensure_test_user(EMAIL, "replacement", db_path)

    assert database.login_user(EMAIL, PASSWORD, "admin", db_path) == admin
    assert database.login_user(EMAIL, "replacement", "admin", db_path) is None


def test_ensure_test_user_preserves_existing_regular_user(db_path):
    user = database.create_user(NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path)

    database.ensure_test_user(EMAIL, "admin-password", db_path)

    assert database.login_user(EMAIL, PASSWORD, "user", db_path) == user[:3]
    assert database.login_user(EMAIL, "admin-password", "admin", db_path) is None


def test_create_booking_persists_all_fields_and_defaults_to_pending(db_path):
    booking_id = add_booking(db_path)

    assert booking_id > 0
    assert database.get_booking_by_id(booking_id, db_path) == (
        booking_id, NAME, EMAIL, ORGANIZATION, "Första hjälpen", 2,
        "Göteborg", "Sal 2", DATE, "pending",
    )
    assert database.get_email_by_booking_id(booking_id, db_path) == EMAIL
    assert database.get_utbildning_by_booking_id(booking_id, db_path) == "Första hjälpen"

    details = database.get_booking_details_by_id(booking_id, db_path)
    assert details["datumtyp"] == "exakt"
    assert details["onskat_datum"] == DATE
    assert details["onskad_ort"] == "Göteborg"
    assert details["faststallt_datum"] is None
    assert details["faststalld_ort"] is None


@pytest.mark.parametrize(
    ("datumtyp", "date_fields", "expected_legacy_date"),
    [
        ("exakt", {"onskat_datum": "2027-02-01"}, "2027-02-01"),
        (
            "intervall",
            {
                "onskat_startdatum": "2027-02-01",
                "onskat_slutdatum": "2027-02-05",
            },
            "2027-02-01 – 2027-02-05",
        ),
    ],
)
def test_create_booking_request_persists_structured_wishes(
    db_path, datumtyp, date_fields, expected_legacy_date
):
    booking_id = database.create_booking_request(
        name=NAME,
        email=EMAIL,
        organization_number=ORGANIZATION,
        utbildning="Säkra lyft",
        antal=4,
        datumtyp=datumtyp,
        utbildningskalla="katalog",
        onskad_ort="  Umeå  ",
        lokalansvar="kund",
        lokaladress="Storgatan 1",
        db_path=db_path,
        **date_fields,
    )

    details = database.get_booking_details_by_id(booking_id, db_path)
    assert details == {
        "id": booking_id,
        "name": NAME,
        "email": EMAIL,
        "organization_number": ORGANIZATION,
        "utbildning": "Säkra lyft",
        "antal": 4,
        "ort": "Umeå",
        "lokal": "Storgatan 1",
        "datum": expected_legacy_date,
        "status": "pending",
        "datumtyp": datumtyp,
        "onskat_datum": date_fields.get("onskat_datum"),
        "onskat_startdatum": date_fields.get("onskat_startdatum"),
        "onskat_slutdatum": date_fields.get("onskat_slutdatum"),
        "utbildningskalla": "katalog",
        "onskad_ort": "Umeå",
        "lokalansvar": "kund",
        "lokaladress": "Storgatan 1",
        "faststallt_datum": None,
        "faststalld_ort": None,
    }
    legacy = database.get_booking_by_id(booking_id, db_path)
    assert len(legacy) == 10
    assert legacy[8:] == (expected_legacy_date, "pending")


def test_create_booking_request_clears_inactive_fields(db_path):
    booking_id = database.create_booking_request(
        name=NAME,
        email=EMAIL,
        organization_number=ORGANIZATION,
        utbildning="Egen kurs",
        antal=1,
        datumtyp="exakt",
        onskat_datum="2027-03-01",
        onskat_startdatum="2027-01-01",
        onskat_slutdatum="2027-01-02",
        utbildningskalla="fritext",
        lokalansvar="sakerhetsutbildarna",
        lokaladress="Ska inte sparas",
        db_path=db_path,
    )

    details = database.get_booking_details_by_id(booking_id, db_path)
    assert details["onskat_startdatum"] is None
    assert details["onskat_slutdatum"] is None
    assert details["lokaladress"] is None
    assert details["lokal"] == "Säkerhetsutbildarna ordnar lokalen"


def test_create_interval_request_round_trips_in_sqlite(tmp_path):
    sqlite_path = tmp_path / "booking-request.db"
    database.create_database(sqlite_path)

    booking_id = database.create_booking_request(
        name=NAME,
        email=EMAIL,
        organization_number=ORGANIZATION,
        utbildning="Egen säkerhetskurs",
        antal=5,
        datumtyp="intervall",
        onskat_startdatum="2027-03-01",
        onskat_slutdatum="2027-03-05",
        utbildningskalla="fritext",
        onskad_ort=None,
        lokalansvar="sakerhetsutbildarna",
        lokaladress="Dold adress",
        db_path=sqlite_path,
    )

    details = database.get_booking_details_by_id(booking_id, sqlite_path)
    assert details["datumtyp"] == "intervall"
    assert details["onskat_datum"] is None
    assert details["onskat_startdatum"] == "2027-03-01"
    assert details["onskat_slutdatum"] == "2027-03-05"
    assert details["utbildningskalla"] == "fritext"
    assert details["onskad_ort"] is None
    assert details["lokalansvar"] == "sakerhetsutbildarna"
    assert details["lokaladress"] is None
    assert database.get_booking_by_id(booking_id, sqlite_path)[8] == (
        "2027-03-01 – 2027-03-05"
    )


def test_booking_queries_return_no_results_for_missing_rows(db_path):
    assert database.get_all_bookings(db_path) == []
    assert database.get_booking_by_name(NAME, db_path) == []
    assert database.get_bookings_by_email(EMAIL, db_path) is None
    assert database.get_bookings_by_status("pending", db_path) is None
    assert database.get_booking_by_id(1, db_path) is None
    assert database.get_booking_details_by_id(1, db_path) is None
    assert database.get_email_by_booking_id(1, db_path) is None
    assert database.get_utbildning_by_booking_id(1, db_path) is None


def test_booking_queries_filter_by_email_name_and_status(db_path):
    first = add_booking(db_path)
    same_email = add_booking(db_path, name="Another name")
    same_name = add_booking(db_path, email="other@example.com")
    database.update_status(first, "cancelled", db_path)

    assert {row[0] for row in database.get_bookings_by_email(EMAIL, db_path)} == {
        first, same_email
    }
    assert {row[0] for row in database.get_booking_by_name(NAME, db_path)} == {
        first, same_name
    }
    assert {row[0] for row in database.get_bookings_by_status("pending", db_path)} == {
        same_email, same_name
    }
    assert [row[0] for row in database.get_bookings_by_status("cancelled", db_path)] == [first]


def test_cancel_booking_only_changes_pending_status(db_path):
    pending = add_booking(db_path)
    accepted = add_booking(db_path)
    database.update_status(accepted, "accepted", db_path)

    assert database.cancel_booking(pending, db_path) == 1
    assert database.cancel_booking(pending, db_path) == 0
    assert database.cancel_booking(accepted, db_path) == 0
    assert database.get_booking_by_id(pending, db_path)[-1] == "cancelled"
    assert database.get_booking_by_id(accepted, db_path)[-1] == "accepted"


def test_cancel_booking_for_email_checks_owner_and_pending_status(db_path):
    booking_id = add_booking(db_path)

    assert database.cancel_booking_for_email(booking_id, "other@example.com", db_path) == 0
    assert database.get_booking_by_id(booking_id, db_path)[-1] == "pending"
    assert database.cancel_booking_for_email(booking_id, EMAIL, db_path) == 1
    assert database.cancel_booking_for_email(booking_id, EMAIL, db_path) == 0


def test_get_all_bookings_sorts_by_course_case_insensitively_then_id(db_path):
    missing = add_booking(db_path, course=None)
    later = add_booking(db_path, course="Zulu")
    first_tie = add_booking(db_path, course="alfa")
    second_tie = add_booking(db_path, course="ALFA")
    third_tie = add_booking(db_path, course="alfa")

    assert [row[0] for row in database.get_all_bookings(db_path)] == [
        first_tie, second_tie, third_tie, later, missing
    ]


def test_book_bookings_updates_only_selected_pending_bookings(db_path):
    first = add_booking(db_path)
    second = add_booking(db_path)
    unselected = add_booking(db_path)
    cancelled = add_booking(db_path)
    accepted = add_booking(db_path)
    database.update_status(cancelled, "cancelled", db_path)
    database.update_status(accepted, "accepted", db_path)
    ids = [first, second, unselected, cancelled, accepted]
    before = {booking_id: database.get_booking_by_id(booking_id, db_path) for booking_id in ids}

    new_date, new_city = "2027-01-15", "Malmö"
    updated = database.book_bookings(
        [first, second, cancelled, accepted], new_date, new_city, db_path
    )

    assert set(updated) == {first, second}

    for booking_id in (first, second):
        original = before[booking_id]
        assert database.get_booking_by_id(booking_id, db_path) == (
            *original[:6], new_city, original[7], new_date, "accepted"
        )
        details = database.get_booking_details_by_id(booking_id, db_path)
        assert details["ort"] == "Göteborg"
        assert details["datum"] == DATE
        assert details["onskad_ort"] == "Göteborg"
        assert details["onskat_datum"] == DATE
        assert details["faststalld_ort"] == new_city
        assert details["faststallt_datum"] == new_date
    for booking_id in (unselected, cancelled, accepted):
        assert database.get_booking_by_id(booking_id, db_path) == before[booking_id]


def test_book_bookings_with_no_selection_leaves_pending_booking_unchanged(db_path):
    booking_id = add_booking(db_path)
    original = database.get_booking_by_id(booking_id, db_path)

    assert database.book_bookings([], "2027-01-15", "Malmö", db_path) == []

    assert database.get_booking_by_id(booking_id, db_path) == original


def test_remove_booking_deletes_only_the_requested_row(db_path):
    removed = add_booking(db_path)
    kept = add_booking(db_path)
    original = database.get_booking_by_id(kept, db_path)

    database.remove_booking(removed, db_path)

    assert database.get_booking_by_id(removed, db_path) is None
    assert database.get_bookings_by_email(EMAIL, db_path) == [original]


LEGACY_BOOKINGS_SCHEMA = """
    CREATE TABLE bookings (
        id {id_column},
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        organization_number TEXT,
        utbildning TEXT,
        antal INTEGER,
        ort TEXT,
        lokal TEXT,
        datum TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
    )
"""

NEW_BOOKING_COLUMNS = [
    "datumtyp",
    "onskat_datum",
    "onskat_startdatum",
    "onskat_slutdatum",
    "utbildningskalla",
    "onskad_ort",
    "lokalansvar",
    "lokaladress",
    "faststallt_datum",
    "faststalld_ort",
]


def _assert_legacy_migration(db_path, pending_id, accepted_id):
    database.create_database(db_path)
    database.create_database(db_path)

    pending = database.get_booking_details_by_id(pending_id, db_path)
    assert pending["datumtyp"] == "exakt"
    assert pending["onskat_datum"] == "2027-04-01"
    assert pending["onskad_ort"] == "Kiruna"
    assert pending["utbildningskalla"] is None
    assert pending["lokalansvar"] is None

    accepted = database.get_booking_details_by_id(accepted_id, db_path)
    assert accepted["datum"] == "2027-05-01"
    assert accepted["ort"] == "Lund"
    for column in NEW_BOOKING_COLUMNS:
        assert accepted[column] is None

    for booking_id in (pending_id, accepted_id):
        legacy = database.get_booking_by_id(booking_id, db_path)
        assert len(legacy) == 10
        assert legacy[-1] in {"pending", "accepted"}


def test_migrates_legacy_postgresql_schema_idempotently(db_path):
    with psycopg.connect(db_path) as connection:
        connection.execute("DROP TABLE bookings")
        connection.execute(
            LEGACY_BOOKINGS_SCHEMA.format(
                id_column="INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
            )
        )
        pending_id = connection.execute(
            """INSERT INTO bookings
               (name, email, organization_number, utbildning, antal, ort, lokal, datum)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (NAME, EMAIL, ORGANIZATION, "HLR", 2, " Kiruna ", "Sal", "2027-04-01"),
        ).fetchone()[0]
        accepted_id = connection.execute(
            """INSERT INTO bookings
               (name, email, organization_number, utbildning, antal, ort, lokal, datum, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted') RETURNING id""",
            (NAME, EMAIL, ORGANIZATION, "HLR", 2, "Lund", "Sal", "2027-05-01"),
        ).fetchone()[0]

    _assert_legacy_migration(db_path, pending_id, accepted_id)

    with psycopg.connect(db_path) as connection:
        columns = [
            row[0]
            for row in connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'bookings'
                   ORDER BY ordinal_position"""
            )
        ]
    assert columns[9:] == ["status", *NEW_BOOKING_COLUMNS]


def test_migrates_legacy_sqlite_schema_idempotently(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            LEGACY_BOOKINGS_SCHEMA.format(
                id_column="INTEGER PRIMARY KEY AUTOINCREMENT"
            )
        )
        pending_id = connection.execute(
            """INSERT INTO bookings
               (name, email, organization_number, utbildning, antal, ort, lokal, datum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (NAME, EMAIL, ORGANIZATION, "HLR", 2, " Kiruna ", "Sal", "2027-04-01"),
        ).lastrowid
        accepted_id = connection.execute(
            """INSERT INTO bookings
               (name, email, organization_number, utbildning, antal, ort, lokal, datum, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted')""",
            (NAME, EMAIL, ORGANIZATION, "HLR", 2, "Lund", "Sal", "2027-05-01"),
        ).lastrowid

    _assert_legacy_migration(db_path, pending_id, accepted_id)

    with sqlite3.connect(db_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(bookings)")]
    assert columns[9:] == ["status", *NEW_BOOKING_COLUMNS]
