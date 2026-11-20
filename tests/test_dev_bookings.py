from collections import Counter
from contextlib import closing
from datetime import date
from pathlib import Path
import secrets
import sqlite3
from unittest.mock import Mock

import psycopg
import pytest

import database
import gen_func


USER_EMAIL = "demo-bookings@example.test"
USER_NAME = "Exempelkund Åsa"
ORGANIZATION = "5566778899"


@pytest.fixture(params=("sqlite", "postgresql"))
def booking_db_path(request, tmp_path):
    if request.param == "postgresql":
        return request.getfixturevalue("db_path")
    path = tmp_path / "dev-bookings.db"
    database.create_database(path)
    return path


@pytest.fixture
def sample_user(booking_db_path):
    return database.create_user(
        USER_NAME, USER_EMAIL, secrets.token_urlsafe(24), ORGANIZATION,
        "user", booking_db_path,
    )


def _details(db_path):
    return {
        row[0]: database.get_booking_details_by_id(row[0], db_path)
        for row in database.get_all_bookings(db_path)
    }


def test_normal_schema_initialization_does_not_create_development_seed_table(booking_db_path):
    database.create_database(booking_db_path)

    if isinstance(booking_db_path, Path):
        with closing(sqlite3.connect(booking_db_path)) as connection:
            row = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'dev_booking_seeds'"
            ).fetchone()
    else:
        with psycopg.connect(booking_db_path) as connection:
            row = connection.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = current_schema() AND tablename = 'dev_booking_seeds'"
            ).fetchone()

    assert row is None
    assert database.get_all_bookings(booking_db_path) == []


def test_development_bookings_cover_customer_and_admin_examples_without_mail(
    booking_db_path, sample_user, monkeypatch
):
    send_mail = Mock(side_effect=AssertionError("Example bookings must not send email"))
    monkeypatch.setattr(gen_func, "send_booked_mail", send_mail)
    monkeypatch.setattr(gen_func, "SMTP", send_mail)

    inserted_ids = database.ensure_dev_bookings(USER_EMAIL, booking_db_path)

    assert len(inserted_ids) == len(set(inserted_ids)) == 4
    rows = _details(booking_db_path)
    assert set(rows) == set(inserted_ids)
    assert Counter(row["status"] for row in rows.values()) == {
        "pending": 2, "accepted": 1, "cancelled": 1,
    }
    assert {row["datumtyp"] for row in rows.values()} == {"exakt", "intervall"}
    assert {row["utbildningskalla"] for row in rows.values()} == {"katalog", "fritext"}
    assert {row["lokalansvar"] for row in rows.values()} == {"kund", "sakerhetsutbildarna"}
    assert any(row["onskad_ort"] is None for row in rows.values())

    for row in rows.values():
        assert (row["name"], row["email"], row["organization_number"]) == (
            USER_NAME, USER_EMAIL, ORGANIZATION,
        )
        assert isinstance(row["antal"], int) and row["antal"] > 0
        assert row["utbildning"]
        assert row["ort"] == row["onskad_ort"]
        if row["datumtyp"] == "exakt":
            assert date.fromisoformat(row["onskat_datum"])
            assert row["datum"] == row["onskat_datum"]
            assert row["onskat_startdatum"] is None
            assert row["onskat_slutdatum"] is None
        else:
            assert row["onskat_datum"] is None
            start = date.fromisoformat(row["onskat_startdatum"])
            end = date.fromisoformat(row["onskat_slutdatum"])
            assert start <= end
            assert row["datum"] == f"{start.isoformat()} – {end.isoformat()}"
        if row["lokalansvar"] == "kund":
            assert row["lokaladress"]
            assert row["lokal"] == row["lokaladress"]
        else:
            assert row["lokaladress"] is None
            assert row["lokal"] == "Säkerhetsutbildarna ordnar lokalen"
        if row["status"] == "accepted":
            assert date.fromisoformat(row["faststallt_datum"])
            assert row["faststalld_ort"]
            display_row = database.get_booking_by_id(row["id"], booking_db_path)
            assert (display_row[6], display_row[8]) == (
                row["faststalld_ort"], row["faststallt_datum"],
            )
        else:
            assert row["faststallt_datum"] is None
            assert row["faststalld_ort"] is None
    send_mail.assert_not_called()


def test_restarting_does_not_reset_changes_or_recreate_deleted_examples(
    booking_db_path, sample_user
):
    database.ensure_dev_bookings(USER_EMAIL, booking_db_path)
    pending = database.get_bookings_by_status("pending", booking_db_path)
    assert database.cancel_booking_for_email(pending[0][0], USER_EMAIL, booking_db_path) == 1
    proposal = database.create_proposal(
        pending[1][0], "2030-06-15", "Ändrad ort", "Ändrad lokal", booking_db_path,
    )
    assert database.respond_to_proposal(
        pending[1][0], proposal["id"], USER_EMAIL, "accept", booking_db_path,
    )
    changed = _details(booking_db_path)

    assert database.ensure_dev_bookings(USER_EMAIL, booking_db_path) == []
    assert _details(booking_db_path) == changed

    for booking_id in changed:
        database.remove_booking(booking_id, booking_db_path)

    assert database.ensure_dev_bookings(USER_EMAIL, booking_db_path) == []
    assert database.get_all_bookings(booking_db_path) == []


def test_examples_preserve_existing_bookings_and_accounts(booking_db_path, sample_user):
    other_email = "other-demo-bookings@example.test"
    other_password = secrets.token_urlsafe(24)
    other_user = database.create_user(
        "Annan kund", other_email, other_password, "1122334455",
        "user", booking_db_path,
    )
    for name, email in ((USER_NAME, USER_EMAIL), ("Annan kund", other_email)):
        database.create_booking(
            name, email, ORGANIZATION, "Befintlig utbildning", 3,
            "Göteborg", "Befintlig lokal", "2030-05-01", booking_db_path,
        )
    original_bookings = _details(booking_db_path)

    inserted_ids = database.ensure_dev_bookings(USER_EMAIL, booking_db_path)

    assert len(inserted_ids) == 4
    assert set(inserted_ids).isdisjoint(original_bookings)
    current_bookings = _details(booking_db_path)
    assert len(current_bookings) == 6
    assert {key: current_bookings[key] for key in original_bookings} == original_bookings
    assert database.authenticate_user(other_email, other_password, booking_db_path) == (
        *other_user[:3], other_email, "user",
    )
    assert database.get_user_role(sample_user[0], booking_db_path) == "user"


@pytest.mark.parametrize("role", (None, "admin"), ids=("missing-user", "admin-user"))
def test_examples_require_an_existing_standard_user(booking_db_path, role):
    if role is not None:
        database.create_user(
            USER_NAME, USER_EMAIL, secrets.token_urlsafe(24), ORGANIZATION,
            role, booking_db_path,
        )

    assert database.ensure_dev_bookings(USER_EMAIL, booking_db_path) == []
    assert database.get_all_bookings(booking_db_path) == []


def test_failed_seeding_rolls_back_bookings_and_allows_retry(
    booking_db_path, sample_user, monkeypatch
):
    original_insert = database._insert_booking
    calls = 0

    def fail_second_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Example insert failed")
        return original_insert(*args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(database, "_insert_booking", fail_second_insert)
        with pytest.raises(RuntimeError, match="Example insert failed"):
            database.ensure_dev_bookings(USER_EMAIL, booking_db_path)

    assert calls == 2
    assert database.get_all_bookings(booking_db_path) == []
    assert len(database.ensure_dev_bookings(USER_EMAIL, booking_db_path)) == 4
    assert len(database.get_all_bookings(booking_db_path)) == 4


def test_each_standard_user_can_receive_examples_once(booking_db_path, sample_user):
    second_email = "second-demo-bookings@example.test"
    database.create_user(
        "Andra exempelkunden", second_email, secrets.token_urlsafe(24),
        "1234567890", "user", booking_db_path,
    )

    first_ids = database.ensure_dev_bookings(USER_EMAIL, booking_db_path)
    second_ids = database.ensure_dev_bookings(second_email, booking_db_path)

    assert len(first_ids) == len(second_ids) == 4
    assert set(first_ids).isdisjoint(second_ids)
    assert len(database.get_bookings_by_email(USER_EMAIL, booking_db_path)) == 4
    assert len(database.get_bookings_by_email(second_email, booking_db_path)) == 4
    assert database.ensure_dev_bookings(USER_EMAIL, booking_db_path) == []
    assert database.ensure_dev_bookings(second_email, booking_db_path) == []
