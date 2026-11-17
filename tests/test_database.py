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


def test_login_rejects_incorrect_email_password_or_role(db_path):
    database.create_user(NAME, EMAIL, PASSWORD, ORGANIZATION, "user", db_path)

    assert database.login_user("other@example.com", PASSWORD, "user", db_path) is None
    assert database.login_user(EMAIL, "wrong-password", "user", db_path) is None
    assert database.login_user(EMAIL, PASSWORD, "admin", db_path) is None


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


def test_booking_queries_return_no_results_for_missing_rows(db_path):
    assert database.get_all_bookings(db_path) == []
    assert database.get_booking_by_name(NAME, db_path) == []
    assert database.get_bookings_by_email(EMAIL, db_path) is None
    assert database.get_bookings_by_status("pending", db_path) is None
    assert database.get_booking_by_id(1, db_path) is None
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
    database.book_bookings([first, second, cancelled, accepted], new_date, new_city, db_path)

    for booking_id in (first, second):
        original = before[booking_id]
        assert database.get_booking_by_id(booking_id, db_path) == (
            *original[:6], new_city, original[7], new_date, "accepted"
        )
    for booking_id in (unselected, cancelled, accepted):
        assert database.get_booking_by_id(booking_id, db_path) == before[booking_id]


def test_book_bookings_with_no_selection_leaves_pending_booking_unchanged(db_path):
    booking_id = add_booking(db_path)
    original = database.get_booking_by_id(booking_id, db_path)

    database.book_bookings([], "2027-01-15", "Malmö", db_path)

    assert database.get_booking_by_id(booking_id, db_path) == original


def test_remove_booking_deletes_only_the_requested_row(db_path):
    removed = add_booking(db_path)
    kept = add_booking(db_path)
    original = database.get_booking_by_id(kept, db_path)

    database.remove_booking(removed, db_path)

    assert database.get_booking_by_id(removed, db_path) is None
    assert database.get_bookings_by_email(EMAIL, db_path) == [original]
