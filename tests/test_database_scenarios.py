"""Databasens gränsfall och transaktionsgarantier på båda databasmotorerna."""

from datetime import datetime

import pytest

import database


EMAIL = "database-scenarios@example.test"


def request_booking(db_path, **overrides):
    values = {
        "name": "Övningskund",
        "email": EMAIL,
        "organization_number": "5566778899",
        "utbildning": "Egen säkerhetsutbildning",
        "antal": 7,
        "datumtyp": "exakt",
        "onskat_datum": "2030-05-03",
        "utbildningskalla": "fritext",
        "onskad_ort": "Umeå",
        "lokalansvar": "kund",
        "lokaladress": "Övningsgatan 1",
        "db_path": db_path,
    }
    values.update(overrides)
    return database.create_booking_request(**values)


def make_proposal(db_path, booking_id, **overrides):
    values = {
        "proposed_date": "2030-05-04",
        "proposed_city": "Lund",
        "proposed_venue": "Sal 1",
    }
    values.update(overrides)
    return database.create_proposal(booking_id, db_path=db_path, **values)


def snapshot(db_path, booking_id):
    return (
        database.get_booking_details_by_id(booking_id, db_path),
        database.get_booking_proposals(booking_id, db_path),
        database.get_booking_history(booking_id, db_path),
    )


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"datumtyp": "okänd"}, "datumtyp"),
        ({"utbildningskalla": "okänd"}, "utbildningskälla"),
        ({"lokalansvar": "okänd"}, "lokalansvar"),
    ],
)
def test_unknown_request_choices_do_not_create_partial_bookings(all_db_path, overrides, error):
    kept = request_booking(all_db_path)
    before = snapshot(all_db_path, kept)

    with pytest.raises(ValueError, match=error):
        request_booking(all_db_path, **overrides)

    assert [row["id"] for row in database.list_booking_details(all_db_path)] == [kept]
    assert snapshot(all_db_path, kept) == before


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"proposed_date": None}, "giltigt datum"),
        ({"proposed_date": ""}, "giltigt datum"),
        ({"proposed_date": "2030-02-29"}, "giltigt datum"),
        ({"proposed_date": "20300504"}, "giltigt datum"),
        ({"proposed_date": "2030-W18-6"}, "giltigt datum"),
        ({"proposed_date": "2030-05-04T10:00:00"}, "giltigt datum"),
        ({"proposed_city": None}, "ort"),
        ({"proposed_city": " \t "}, "ort"),
        ({"proposed_city": "Ö" * 201}, "ort"),
        ({"proposed_venue": None}, "lokal"),
        ({"proposed_venue": " \n "}, "lokal"),
        ({"proposed_venue": "Å" * 1001}, "lokal"),
    ],
)
def test_invalid_proposal_does_not_change_request_or_history(all_db_path, overrides, error):
    booking_id = request_booking(all_db_path)
    before = snapshot(all_db_path, booking_id)

    with pytest.raises(ValueError, match=error):
        make_proposal(all_db_path, booking_id, **overrides)

    assert snapshot(all_db_path, booking_id) == before


def test_proposal_accepts_leap_day_length_boundaries_and_trims_text(all_db_path):
    booking_id = request_booking(all_db_path)
    city, venue = "Ö" * 198, "Å" * 998

    proposal = make_proposal(
        all_db_path, booking_id, proposed_date="2032-02-29",
        proposed_city=f" {city} ", proposed_venue=f" {venue} ",
    )

    assert proposal["proposed_date"] == "2032-02-29"
    assert proposal["proposed_city"] == city
    assert proposal["proposed_venue"] == venue
    assert database.get_booking_proposals(booking_id, all_db_path) == [proposal]


@pytest.mark.parametrize("note", [None, 123, "", " \n\t ", "a" * 5001])
def test_invalid_notes_leave_both_public_and_private_history_unchanged(all_db_path, note):
    booking_id = request_booking(all_db_path)
    before = snapshot(all_db_path, booking_id)

    assert database.add_booking_note(booking_id, note, all_db_path) is False

    assert snapshot(all_db_path, booking_id) == before


def test_note_at_maximum_length_is_saved_only_in_admin_history(all_db_path):
    booking_id = request_booking(all_db_path)
    note = "Ö" * 5000
    public_history = database.get_booking_history(booking_id, all_db_path, include_private=False)

    assert database.add_booking_note(booking_id, note, all_db_path, actor_id=42)

    private = database.get_booking_history(booking_id, all_db_path)[-1]
    assert (private["note"], private["actor_id"], private["kind"], private["is_private"]) == (
        note, 42, "note", 1,
    )
    assert datetime.fromisoformat(private["created_at"]).utcoffset().total_seconds() == 0
    assert database.get_booking_history(booking_id, all_db_path, include_private=False) == public_history


def test_booking_insert_rolls_back_when_initial_history_cannot_be_saved(all_db_path, monkeypatch):
    def fail_history(*args, **kwargs):
        raise RuntimeError("Historiken kunde inte sparas i testet")

    with monkeypatch.context() as context:
        context.setattr(database, "_record_history", fail_history)
        with pytest.raises(RuntimeError, match="Historiken"):
            request_booking(all_db_path)

    assert database.list_booking_details(all_db_path) == []
    booking_id = request_booking(all_db_path)
    assert len(database.get_booking_history(booking_id, all_db_path)) == 1


@pytest.mark.parametrize("operation", ["proposal", "accept", "cancel", "delivery"])
def test_failed_history_write_rolls_back_the_whole_action_and_allows_retry(
    all_db_path, monkeypatch, operation,
):
    booking_id = request_booking(all_db_path)
    if operation == "proposal":
        action = lambda: make_proposal(all_db_path, booking_id)
    elif operation == "cancel":
        action = lambda: database.cancel_booking_for_email(booking_id, EMAIL, all_db_path)
    else:
        proposal = make_proposal(all_db_path, booking_id)
        if operation == "accept":
            action = lambda: database.respond_to_proposal(
                booking_id, proposal["id"], EMAIL, "accept", all_db_path,
            )
        else:
            assert database.claim_proposal_delivery(proposal["id"], all_db_path)
            action = lambda: database.finish_proposal_delivery(proposal["id"], "sent", all_db_path)
    before = snapshot(all_db_path, booking_id)

    def fail_history(*args, **kwargs):
        raise RuntimeError("Historiken kunde inte sparas i testet")

    with monkeypatch.context() as context:
        context.setattr(database, "_record_history", fail_history)
        with pytest.raises(RuntimeError, match="Historiken"):
            action()

    assert snapshot(all_db_path, booking_id) == before
    assert action()
    assert snapshot(all_db_path, booking_id) != before


@pytest.mark.parametrize("delivery_status", ["sending", "pending", "unknown"])
def test_invalid_delivery_result_preserves_active_claim(all_db_path, delivery_status):
    booking_id = request_booking(all_db_path)
    proposal = make_proposal(all_db_path, booking_id)
    assert database.claim_proposal_delivery(proposal["id"], all_db_path)
    before = snapshot(all_db_path, booking_id)

    with pytest.raises(ValueError, match="utskicksresultat"):
        database.finish_proposal_delivery(proposal["id"], delivery_status, all_db_path)

    assert snapshot(all_db_path, booking_id) == before
    assert database.finish_proposal_delivery(proposal["id"], "sent", all_db_path)


def test_missing_ids_are_safe_for_reads_and_mutations(all_db_path):
    kept = request_booking(all_db_path)
    before = snapshot(all_db_path, kept)
    missing = kept + 1000

    assert database.get_booking_proposals(missing, all_db_path) == []
    assert database.get_booking_history(missing, all_db_path) == []
    assert database.get_user_identity(missing, all_db_path) is None
    assert make_proposal(all_db_path, missing) is None
    assert database.transition_booking(missing, "registered", all_db_path) is False
    assert database.respond_to_proposal(missing, missing, EMAIL, "accept", all_db_path) is False
    assert database.add_booking_note(missing, "Anteckning", all_db_path) is False
    assert database.claim_proposal_delivery(missing, all_db_path) is False
    assert database.finish_proposal_delivery(missing, "sent", all_db_path) is False
    database.remove_booking(missing, all_db_path)

    assert snapshot(all_db_path, kept) == before


def test_removing_booking_cascades_only_its_proposals_and_history(all_db_path):
    removed = request_booking(all_db_path)
    kept = request_booking(all_db_path, email="kept@example.test")
    removed_proposal = make_proposal(all_db_path, removed)
    make_proposal(all_db_path, kept)
    assert database.add_booking_note(removed, "Intern anteckning", all_db_path)
    before = snapshot(all_db_path, kept)

    database.remove_booking(removed, all_db_path)

    assert database.get_booking_details_by_id(removed, all_db_path) is None
    assert database.get_booking_proposals(removed, all_db_path) == []
    assert database.get_booking_history(removed, all_db_path) == []
    assert database.claim_proposal_delivery(removed_proposal["id"], all_db_path) is False
    assert snapshot(all_db_path, kept) == before


def test_list_filters_combine_owner_and_status_without_exposing_private_events(all_db_path):
    selected = request_booking(all_db_path)
    request_booking(all_db_path, email="other@example.test")
    registered = request_booking(all_db_path)
    assert database.transition_booking(registered, "registered", all_db_path)
    assert database.add_booking_note(selected, "Privat administratörsanteckning", all_db_path)

    customer_rows = database.list_booking_details(all_db_path, email=EMAIL, status="pending")
    admin_rows = database.list_booking_details(all_db_path, status="pending")

    assert [row["id"] for row in customer_rows] == [selected]
    assert customer_rows[0]["latest_event"] == "Bokningsförfrågan skapad."
    assert customer_rows[0]["latest_event_at"]
    assert next(row for row in admin_rows if row["id"] == selected)["latest_event"] == (
        "Privat administratörsanteckning"
    )
    assert database.list_booking_details(all_db_path, email="", status="pending") == []
    assert database.list_booking_details(all_db_path, email=EMAIL, status="unknown") == []


def test_list_prioritizes_actionable_requests_and_newer_ids_within_status(all_db_path):
    ids = {}
    for status in ("pending", "registered", "awaiting_response", "new_date_requested", "accepted", "rescheduled", "completed", "cancelled"):
        booking_id = request_booking(all_db_path)
        ids[status] = booking_id
        if status == "registered":
            assert database.transition_booking(booking_id, "registered", all_db_path)
        elif status == "cancelled":
            assert database.cancel_booking_for_email(booking_id, EMAIL, all_db_path)
        elif status != "pending":
            proposal = make_proposal(all_db_path, booking_id)
            if status == "new_date_requested":
                assert database.respond_to_proposal(booking_id, proposal["id"], EMAIL, "new_date", all_db_path)
            elif status != "awaiting_response":
                assert database.respond_to_proposal(booking_id, proposal["id"], EMAIL, "accept", all_db_path)
                if status in {"rescheduled", "completed"}:
                    assert database.transition_booking(booking_id, status, all_db_path)
    newest_pending = request_booking(all_db_path)

    assert [row["id"] for row in database.list_booking_details(all_db_path)] == [
        ids["new_date_requested"], newest_pending, ids["pending"], ids["registered"],
        ids["awaiting_response"], ids["accepted"], ids["rescheduled"], ids["cancelled"], ids["completed"],
    ]


def test_sql_metacharacters_are_stored_and_filtered_as_literal_data(all_db_path):
    name = "Öberg'); DELETE FROM bookings; --"
    email = "literal' OR '1'='1@example.test"
    course = "Kurs med 'citat', % och _"
    selected = request_booking(all_db_path, name=name, email=email, utbildning=course)
    other = request_booking(all_db_path)

    assert database.get_booking_by_name(name, all_db_path)[0][0] == selected
    assert database.get_bookings_by_email(email, all_db_path)[0][0] == selected
    assert [row["id"] for row in database.list_booking_details(all_db_path, email=email)] == [selected]
    assert database.get_utbildning_by_booking_id(selected, all_db_path) == course
    assert database.get_bookings_by_status("pending' OR '1'='1", all_db_path) is None
    assert database.list_booking_details(all_db_path, email="' OR 1=1 --") == []
    assert {row[0] for row in database.get_all_bookings(all_db_path)} == {selected, other}


def test_user_identity_reads_current_database_values_and_disappears_after_deletion(all_db_path):
    user = database.create_user("Första namnet", EMAIL, "test-only-password", "1111111111", "user", all_db_path)
    with database._cursor(all_db_path) as cursor:
        database._execute(
            cursor, "UPDATE users SET name = %s, email = %s, organization_number = %s, role = %s WHERE id = %s",
            ("Ändrat namn", "updated@example.test", "2222222222", "admin", user[0]),
        )

    assert database.get_user_identity(user[0], all_db_path) == {
        "id": user[0], "name": "Ändrat namn", "email": "updated@example.test",
        "organization_number": "2222222222", "role": "admin",
    }
    assert database.check_user_exists(EMAIL, all_db_path) is False
    assert database.check_user_exists("updated@example.test", all_db_path) is True
    with database._cursor(all_db_path) as cursor:
        database._execute(cursor, "DELETE FROM users WHERE id = %s", (user[0],))
    assert database.get_user_identity(user[0], all_db_path) is None
    assert database.get_user_role(user[0], all_db_path) is None
    assert database.authenticate_user("updated@example.test", "test-only-password", all_db_path) is None


@pytest.mark.parametrize("stored_password", ["scrypt:invalid$salt$hash", "scrypt:1:bad:1$salt$hash", "pbkdf2:unknown:1$salt$hash"])
def test_malformed_password_hashes_reject_login_without_falling_back_to_plaintext(all_db_path, stored_password):
    database.create_user("Kund", EMAIL, stored_password, "5566778899", "user", all_db_path)

    assert database.authenticate_user(EMAIL, "test-only-password", all_db_path) is None
    assert database.authenticate_user(EMAIL, stored_password, all_db_path) is None
