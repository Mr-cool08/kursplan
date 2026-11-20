from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import sqlite3

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

import database


EMAIL = "workflow-owner@example.test"


@pytest.fixture(params=("sqlite", "postgresql"))
def workflow_db(request, tmp_path):
    if request.param == "postgresql":
        return request.getfixturevalue("db_path")
    path = tmp_path / "workflow.db"
    database.create_database(path)
    return path


def request_booking(db):
    return database.create_booking_request(
        name="Bokningskund", email=EMAIL, organization_number="5566778899",
        utbildning="Egen utbildning", antal=12, datumtyp="intervall",
        onskat_startdatum="2027-05-01", onskat_slutdatum="2027-05-05",
        utbildningskalla="fritext", onskad_ort=None,
        lokalansvar="kund", lokaladress="Kundens gata 1", db_path=db,
    )


def proposal(db, booking_id, day="2027-05-03"):
    return database.create_proposal(booking_id, day, "Göteborg", "Sal 1", db)


def accept_booking(db):
    booking_id = request_booking(db)
    current = proposal(db, booking_id)
    assert database.respond_to_proposal(booking_id, current["id"], EMAIL, "accept", db)
    return booking_id


def test_request_and_registration_have_history_and_no_confirmed_date(workflow_db):
    user = database.create_user("Kund", EMAIL, "test-password", "5566778899", "user", workflow_db)
    booking_id = request_booking(workflow_db)
    original = database.get_booking_details_by_id(booking_id, workflow_db)
    assert original["status"] == "pending"
    assert original["faststallt_datum"] is None
    assert original["faststalld_lokal"] is None
    history = database.get_booking_history(booking_id, workflow_db)
    assert len(history) == 1
    assert history[0]["actor"] == "customer"
    assert history[0]["actor_id"] == user[0]
    assert database.transition_booking(booking_id, "registered", workflow_db, actor_id=42)
    assert not database.transition_booking(booking_id, "registered", workflow_db)
    registered = database.get_booking_details_by_id(booking_id, workflow_db)
    assert registered == {**original, "status": "registered"}
    assert database.get_booking_proposals(booking_id, workflow_db) == []
    assert database.get_booking_history(booking_id, workflow_db)[-1]["actor_id"] == 42


@pytest.mark.parametrize("registered", (False, True))
def test_proposals_from_new_or_registered_are_never_confirmation(workflow_db, registered):
    booking_id = request_booking(workflow_db)
    if registered:
        assert database.transition_booking(booking_id, "registered", workflow_db)
    current = proposal(workflow_db, booking_id)
    assert current["state"] == "pending"
    assert current["delivery_status"] == "pending"
    assert current["sent_at"] is None
    booking = database.get_booking_details_by_id(booking_id, workflow_db)
    assert booking["status"] == "awaiting_response"
    assert booking["faststallt_datum"] is None
    assert proposal(workflow_db, booking_id) is None
    assert len(database.get_booking_proposals(booking_id, workflow_db)) == 1


def test_repeated_rejections_keep_same_booking_and_stale_proposals_cannot_be_accepted(workflow_db):
    booking_id = request_booking(workflow_db)
    original = database.get_booking_details_by_id(booking_id, workflow_db)
    old_ids = []
    for day in ("2027-05-02", "2027-05-03"):
        current = proposal(workflow_db, booking_id, day)
        old_ids.append(current["id"])
        assert database.respond_to_proposal(booking_id, current["id"], EMAIL, "new_date", workflow_db)
        assert database.get_booking_details_by_id(booking_id, workflow_db)["status"] == "new_date_requested"
        assert database.get_booking_history(booking_id, workflow_db)[-1]["note"] == "Kunden har begärt ett nytt datumförslag."
    current = proposal(workflow_db, booking_id, "2027-05-04")
    for old_id in old_ids:
        assert not database.respond_to_proposal(booking_id, old_id, EMAIL, "accept", workflow_db)
    assert database.respond_to_proposal(booking_id, current["id"], EMAIL, "accept", workflow_db)
    final = database.get_booking_details_by_id(booking_id, workflow_db)
    assert final["id"] == booking_id
    assert final["status"] == "accepted"
    assert (final["faststallt_datum"], final["faststalld_ort"], final["faststalld_lokal"]) == (
        "2027-05-04", "Göteborg", "Sal 1",
    )
    for field in ("datum", "datumtyp", "onskat_datum", "onskat_startdatum", "onskat_slutdatum", "ort", "onskad_ort", "lokal", "lokaladress"):
        assert final[field] == original[field]
    proposals = database.get_booking_proposals(booking_id, workflow_db)
    assert [item["state"] for item in proposals] == ["declined", "declined", "accepted"]
    assert all(item["responded_at"] for item in proposals)
    assert not database.respond_to_proposal(booking_id, current["id"], EMAIL, "accept", workflow_db)


def test_owner_is_checked_at_mutation_and_wrong_booking_id_cannot_select_a_proposal(workflow_db):
    first, second = request_booking(workflow_db), request_booking(workflow_db)
    current = proposal(workflow_db, first)
    second_proposal = proposal(workflow_db, second)
    for action in ("accept", "new_date", "cancel"):
        assert not database.respond_to_proposal(first, current["id"], "outsider@example.test", action, workflow_db)
        assert not database.respond_to_proposal(first, second_proposal["id"], EMAIL, action, workflow_db)
    assert not database.transition_booking(first, "cancelled", workflow_db, actor="customer", email="outsider@example.test")
    assert database.get_booking_details_by_id(first, workflow_db)["status"] == "awaiting_response"
    assert len(database.get_booking_history(first, workflow_db)) == 2


@pytest.mark.parametrize("source", ("pending", "registered", "new_date_requested", "awaiting_response"))
def test_customer_cancellation_preserves_history_and_deactivates_proposals(workflow_db, source):
    booking_id = request_booking(workflow_db)
    if source == "registered":
        database.transition_booking(booking_id, "registered", workflow_db)
    elif source in {"awaiting_response", "new_date_requested"}:
        current = proposal(workflow_db, booking_id)
        if source == "new_date_requested":
            database.respond_to_proposal(booking_id, current["id"], EMAIL, "new_date", workflow_db)
    assert database.transition_booking(booking_id, "cancelled", workflow_db, actor="customer", email=EMAIL)
    assert not database.transition_booking(booking_id, "cancelled", workflow_db, actor="customer", email=EMAIL)
    assert database.get_booking_details_by_id(booking_id, workflow_db)["status"] == "cancelled"
    for old in database.get_booking_proposals(booking_id, workflow_db):
        assert old["state"] != "pending"
        assert not database.respond_to_proposal(booking_id, old["id"], EMAIL, "accept", workflow_db)
    assert database.get_booking_history(booking_id, workflow_db)[-1]["to_status"] == "cancelled"


@pytest.mark.parametrize("actor", ("admin", "customer"))
def test_cancellation_records_customer_response_only_when_customer_cancelled(workflow_db, actor):
    booking_id = request_booking(workflow_db)
    current = proposal(workflow_db, booking_id)
    assert database.transition_booking(
        booking_id, "cancelled", workflow_db, actor=actor,
        email=EMAIL if actor == "customer" else None, actor_id=42,
    )
    saved = database.get_booking_proposals(booking_id, workflow_db)[0]
    assert saved["state"] == "cancelled"
    assert saved["response"] == ("cancel" if actor == "customer" else None)
    assert bool(saved["responded_at"]) == (actor == "customer")
    history = database.get_booking_history(booking_id, workflow_db)[-1]
    assert history["actor"] == actor
    assert history["actor_id"] == 42
    assert history["to_status"] == "cancelled"
    assert not database.respond_to_proposal(booking_id, current["id"], EMAIL, "accept", workflow_db)


@pytest.mark.parametrize("target", ("completed", "cancelled"))
@pytest.mark.parametrize("reschedule", (False, True))
def test_post_acceptance_statuses_keep_date_and_proposals_unchanged(workflow_db, target, reschedule):
    booking_id = accept_booking(workflow_db)
    before = database.get_booking_details_by_id(booking_id, workflow_db)
    proposals = database.get_booking_proposals(booking_id, workflow_db)
    if reschedule:
        assert database.transition_booking(booking_id, "rescheduled", workflow_db)
    assert database.transition_booking(booking_id, target, workflow_db)
    final = database.get_booking_details_by_id(booking_id, workflow_db)
    assert final == {**before, "status": target}
    assert database.get_booking_proposals(booking_id, workflow_db) == proposals
    assert proposal(workflow_db, booking_id) is None
    assert not database.transition_booking(booking_id, "accepted", workflow_db)


def test_only_the_declared_role_specific_transitions_are_permitted(workflow_db):
    booking_id = request_booking(workflow_db)
    for status in ("accepted", "completed", "rescheduled", "cancelled", "awaiting_response", "new_date_requested", "bogus"):
        assert not database.transition_booking(booking_id, status, workflow_db)
    assert not database.transition_booking(booking_id, "registered", workflow_db, actor="customer", email=EMAIL)
    assert not database.transition_booking(booking_id, "cancelled", workflow_db, actor="customer")
    assert not database.update_status(booking_id, "accepted", workflow_db)
    with pytest.raises(ValueError, match="datumförslag"):
        database.book_bookings([booking_id], "2027-05-03", "Göteborg", workflow_db)
    current = proposal(workflow_db, booking_id)
    assert not database.respond_to_proposal(booking_id, current["id"], EMAIL, "unknown", workflow_db)
    assert database.transition_booking(booking_id, "cancelled", workflow_db)


def test_private_admin_notes_are_hidden_from_customer_history_and_latest_event(workflow_db):
    booking_id = request_booking(workflow_db)
    assert database.add_booking_note(booking_id, "  Intern anteckning  ", workflow_db, actor_id=42)
    assert not database.add_booking_note(booking_id, " ", workflow_db)
    assert not database.add_booking_note(-1, "Anteckning", workflow_db)
    history = database.get_booking_history(booking_id, workflow_db)
    assert history[-1]["note"] == "Intern anteckning"
    assert history[-1]["is_private"] == 1
    assert history[-1]["actor_id"] == 42
    assert len(database.get_booking_history(booking_id, workflow_db, include_private=False)) == 1
    assert database.list_booking_details(workflow_db)[0]["latest_event"] == "Intern anteckning"
    assert database.list_booking_details(workflow_db, email=EMAIL)[0]["latest_event"] == "Bokningsförfrågan skapad."
    assert database.list_booking_details(workflow_db, email="outsider@example.test") == []


@pytest.mark.parametrize("result", ("sent", "failed", "disabled"))
def test_mail_claim_and_result_are_explicit_and_retries_cannot_double_send(workflow_db, result):
    booking_id = request_booking(workflow_db)
    current = proposal(workflow_db, booking_id)
    assert database.claim_proposal_delivery(current["id"], workflow_db)
    assert not database.claim_proposal_delivery(current["id"], workflow_db)
    assert database.finish_proposal_delivery(current["id"], result, workflow_db)
    saved = database.get_booking_proposals(booking_id, workflow_db)[0]
    assert saved["delivery_status"] == result
    assert bool(saved["sent_at"]) == (result == "sent")
    assert database.get_booking_details_by_id(booking_id, workflow_db)["status"] == "awaiting_response"
    if result == "sent":
        assert not database.claim_proposal_delivery(current["id"], workflow_db)
    else:
        assert database.claim_proposal_delivery(current["id"], workflow_db)
        assert database.finish_proposal_delivery(current["id"], "sent", workflow_db)
    assert not database.finish_proposal_delivery(current["id"], "failed", workflow_db)
    assert database.respond_to_proposal(booking_id, current["id"], EMAIL, "new_date", workflow_db)
    assert not database.claim_proposal_delivery(current["id"], workflow_db)


def run_together(*operations):
    barrier = Barrier(len(operations))
    def run(operation):
        barrier.wait(timeout=15)
        return operation()
    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        return list(executor.map(run, operations))


def test_simultaneous_proposals_create_only_one_active_proposal(workflow_db):
    booking_id = request_booking(workflow_db)
    results = run_together(
        lambda: proposal(workflow_db, booking_id, "2027-05-03"),
        lambda: proposal(workflow_db, booking_id, "2027-05-04"),
    )
    assert sum(item is not None for item in results) == 1
    assert len(database.get_booking_proposals(booking_id, workflow_db)) == 1
    assert len(database.get_booking_history(booking_id, workflow_db)) == 2


def test_outdated_preview_is_rejected_under_the_booking_lock(workflow_db):
    booking_id = request_booking(workflow_db)
    current = proposal(workflow_db, booking_id)
    assert database.respond_to_proposal(booking_id, current["id"], EMAIL, "new_date", workflow_db)
    assert database.create_proposal(
        booking_id, "2027-05-04", "Lund", "Sal 2", workflow_db,
        expected_status="pending", expected_previous_proposal_id=None,
    ) is None
    assert database.create_proposal(
        booking_id, "2027-05-04", "Lund", "Sal 2", workflow_db,
        expected_status="new_date_requested", expected_previous_proposal_id=None,
    ) is None
    assert database.create_proposal(
        booking_id, "2027-05-04", "Lund", "Sal 2", workflow_db,
        expected_status="new_date_requested", expected_previous_proposal_id=current["id"],
    ) is not None


def test_simultaneous_customer_responses_have_exactly_one_winner(workflow_db):
    booking_id = request_booking(workflow_db)
    current = proposal(workflow_db, booking_id)
    results = run_together(*(
        lambda action=action: database.respond_to_proposal(booking_id, current["id"], EMAIL, action, workflow_db)
        for action in ("accept", "new_date", "cancel")
    ))
    assert sum(results) == 1
    assert len(database.get_booking_history(booking_id, workflow_db)) == 3
    saved = database.get_booking_proposals(booking_id, workflow_db)[0]
    expected_status = {"accept": "accepted", "new_date": "new_date_requested", "cancel": "cancelled"}[saved["response"]]
    assert database.get_booking_details_by_id(booking_id, workflow_db)["status"] == expected_status


def test_simultaneous_delivery_claims_have_exactly_one_winner(workflow_db):
    booking_id = request_booking(workflow_db)
    current = proposal(workflow_db, booking_id)
    assert sum(run_together(*(
        lambda: database.claim_proposal_delivery(current["id"], workflow_db)
        for _ in range(3)
    ))) == 1


def test_additive_migration_preserves_legacy_ids_users_status_and_meaning(workflow_db, tmp_path):
    if isinstance(workflow_db, Path):
        legacy = tmp_path / "legacy-workflow.db"
        connection = sqlite3.connect(legacy)
        id_column = "INTEGER PRIMARY KEY AUTOINCREMENT"
    else:
        # This schema is inside pytest's isolated throwaway database.
        with psycopg.connect(workflow_db) as conn:
            conn.execute("CREATE SCHEMA workflow_legacy")
        legacy = make_conninfo(workflow_db, options="-c search_path=workflow_legacy")
        connection = psycopg.connect(legacy)
        id_column = "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    with connection:
        cursor = connection.cursor()
        database._execute(cursor, f"CREATE TABLE bookings (id {id_column}, name TEXT NOT NULL, "
                          "email TEXT NOT NULL, organization_number TEXT, utbildning TEXT, antal INTEGER, "
                          "ort TEXT, lokal TEXT, datum TEXT, status TEXT NOT NULL DEFAULT 'pending')")
        database._execute(cursor, f"CREATE TABLE users (id {id_column}, name TEXT NOT NULL, "
                          "organization_number TEXT, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT, "
                          "status TEXT NOT NULL DEFAULT 'pending')")
        database._execute(cursor, "INSERT INTO users (name,email,password,role) VALUES (%s,%s,%s,'user') RETURNING id",
                          ("Befintlig kund", EMAIL, "legacy-test-password"))
        user_id = cursor.fetchone()[0]
        booking_ids = []
        for status in ("pending", "accepted", "cancelled"):
            database._execute(cursor, "INSERT INTO bookings (name,email,utbildning,antal,ort,lokal,datum,status) "
                              "VALUES (%s,%s,'HLR',5,'Lund','Bevarad lokal','2027-05-03',%s) RETURNING id",
                              ("Befintlig kund", EMAIL, status))
            booking_ids.append(cursor.fetchone()[0])
    connection.close()
    database.create_database(legacy)
    first_history = {key: database.get_booking_history(key, legacy) for key in booking_ids}
    database.create_database(legacy)
    assert database.authenticate_user(EMAIL, "legacy-test-password", legacy)[0] == user_id
    assert {row["id"] for row in database.list_booking_details(legacy)} == set(booking_ids)
    for booking_id, status in zip(booking_ids, ("pending", "accepted", "cancelled")):
        booking = database.get_booking_details_by_id(booking_id, legacy)
        assert booking["status"] == status
        assert booking["datum"] == "2027-05-03"
        assert booking["lokal"] == "Bevarad lokal"
        assert database.get_booking_history(booking_id, legacy) == first_history[booking_id]
        assert len(first_history[booking_id]) == 1
        assert first_history[booking_id][0]["kind"] == "baseline"
        assert database.get_booking_proposals(booking_id, legacy) == []
        if status == "accepted":
            assert (booking["faststallt_datum"], booking["faststalld_ort"], booking["faststalld_lokal"]) == (
                "2027-05-03", "Lund", "Bevarad lokal",
            )
            assert booking["onskat_datum"] is None
            assert booking["onskad_ort"] is None
        elif status == "pending":
            assert booking["onskat_datum"] == "2027-05-03"
            assert booking["onskad_ort"] == "Lund"
