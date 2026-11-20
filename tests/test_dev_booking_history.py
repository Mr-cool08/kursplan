"""Exempelhistorik vid utvecklingsstart ska vara avgränsad och beständig."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

import company_lookup
import database
import gen_func


PASSWORD = "dev-history-test-password"


@pytest.fixture
def demo_accounts(all_db_path):
    admin = database.create_user(
        "Utvecklingsadmin Åsa", "dev-history-admin@example.test", PASSWORD,
        "5566778899", "admin", all_db_path,
    )
    customer = database.create_user(
        "Exempelkund Bo", "dev-history-customer@example.test", PASSWORD,
        "5566778899", "user", all_db_path,
    )
    other = database.create_user(
        "Annan kund Cecilia", "dev-history-other@example.test", PASSWORD,
        "1122334455", "user", all_db_path,
    )
    return admin, customer, other


def _sql(path, query, params=()):
    with database._cursor(path) as cursor:
        database._execute(cursor, query, params)
        return cursor.fetchall() if cursor.description else []


def _new_booking(path, customer, status="pending"):
    booking_id = database.create_booking(
        customer[1], customer[3], customer[2], "HLR", 5, "Örebro", "Sal 1",
        "2030-08-20", path,
    )
    if status != "pending":
        _sql(path, "UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))
    return booking_id


def _snapshot(path, booking_id):
    return (
        database.get_booking_details_by_id(booking_id, path),
        database.get_booking_proposals(booking_id, path),
        database.get_booking_history(booking_id, path, include_actor_details=True),
    )


def _seed_table_exists(path):
    if isinstance(path, Path):
        rows = _sql(path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'dev_booking_history_seeds'")
    else:
        rows = _sql(path, "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = 'dev_booking_history_seeds'")
    return bool(rows)


def _signed_client(app, account):
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(user_id=account[0], user_name="Föråldrat sessionsnamn",
                     user_email=account[3])
    return client


def test_every_booking_status_and_customer_receives_marked_history(all_db_path, demo_accounts):
    admin, customer, other = demo_accounts
    bookings = {
        status: _new_booking(all_db_path, customer if index % 2 else other, status)
        for index, status in enumerate(database.BOOKING_STATUSES)
    }
    before = {booking_id: _snapshot(all_db_path, booking_id) for booking_id in bookings.values()}

    seeded = database.ensure_dev_booking_history(admin[3], all_db_path)

    assert set(seeded) == set(bookings.values())
    assert len(seeded) == len(bookings)
    for status, booking_id in bookings.items():
        booking, proposals, history = _snapshot(all_db_path, booking_id)
        old_booking, old_proposals, old_history = before[booking_id]
        assert (booking, proposals) == (old_booking, old_proposals)
        assert history[:len(old_history)] == old_history
        examples = history[len(old_history):]
        assert len(examples) == 3
        assert [event["kind"] for event in examples] == ["demo", "demo", "note"]
        assert [event["is_private"] for event in examples] == [0, 0, 1]
        assert all(event["note"].startswith("Exempeldata: ") for event in examples)
        assert all(event["from_status"] == event["to_status"] == status for event in examples)
        assert all(datetime.fromisoformat(event["created_at"]).utcoffset() is not None for event in examples)
        for event in (examples[0], examples[2]):
            assert (event["actor"], event["actor_id"], event["actor_name"]) == (
                "admin", admin[0], admin[1],
            )
        if status in {"pending", "accepted", "new_date_requested"}:
            owner = customer if booking["email"] == customer[3] else other
            assert (examples[1]["actor"], examples[1]["actor_id"], examples[1]["actor_name"]) == (
                "customer", owner[0], owner[1],
            )
        else:
            assert examples[1]["actor"] == "admin"
    accepted = _snapshot(all_db_path, bookings["accepted"])[2][-2]
    assert "accepterat datumförslaget" in accepted["note"]
    assert "slutförd" in _snapshot(all_db_path, bookings["completed"])[2][-2]["note"]


def test_restarts_preserve_edits_and_seed_only_new_bookings(all_db_path, demo_accounts):
    admin, customer, other = demo_accounts
    first = _new_booking(all_db_path, customer)
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == [first]
    assert database.transition_booking(first, "registered", all_db_path, actor_id=admin[0])
    assert database.add_booking_note(first, "En riktig anteckning efter exempeldata.",
                                     all_db_path, actor_id=admin[0])
    saved = _snapshot(all_db_path, first)

    assert database.ensure_dev_booking_history(admin[3], all_db_path) == []
    assert _snapshot(all_db_path, first) == saved
    second = _new_booking(all_db_path, other)
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == [second]
    assert _snapshot(all_db_path, first) == saved
    assert len(_snapshot(all_db_path, second)[2]) == 4
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == []


def test_seed_preserves_real_proposals_responses_and_private_history(all_db_path, demo_accounts):
    admin, customer, _ = demo_accounts
    booking_id = _new_booking(all_db_path, customer)
    proposal = database.create_proposal(
        booking_id, "2030-08-24", "Växjö", "Sal 2", all_db_path, actor_id=admin[0],
    )
    assert database.respond_to_proposal(booking_id, proposal["id"], customer[3], "accept",
                                       all_db_path, actor_id=customer[0])
    assert database.add_booking_note(booking_id, "Ursprunglig intern anteckning.",
                                     all_db_path, actor_id=admin[0])
    before_booking, before_proposals, before_history = _snapshot(all_db_path, booking_id)

    assert database.ensure_dev_booking_history(admin[3], all_db_path) == [booking_id]

    booking, proposals, history = _snapshot(all_db_path, booking_id)
    assert (booking, proposals) == (before_booking, before_proposals)
    assert history[:-3] == before_history


def test_customer_sees_admin_name_but_not_fake_private_note(
    all_db_path, demo_accounts, scenario_app,
):
    admin, customer, other = demo_accounts
    booking_id = _new_booking(all_db_path, customer)
    database.ensure_dev_booking_history(admin[3], all_db_path)
    events = _snapshot(all_db_path, booking_id)[2][-3:]
    customer_client = _signed_client(scenario_app, customer)
    response = customer_client.get(f"/bookings/{booking_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert admin[1] in html and events[0]["note"] in html
    assert events[-1]["note"] not in html and "Konto #" not in html
    assert _signed_client(scenario_app, other).get(f"/bookings/{booking_id}").status_code == 404
    admin_html = _signed_client(scenario_app, admin).get(f"/admin/bookings/{booking_id}").get_data(as_text=True)
    assert events[-1]["note"] in admin_html
    assert f"Konto #{admin[0]}" in admin_html


def test_history_uses_account_snapshot_even_after_rename(all_db_path, demo_accounts):
    admin, customer, _ = demo_accounts
    booking_id = _new_booking(all_db_path, customer)
    database.ensure_dev_booking_history(admin[3], all_db_path)
    _sql(all_db_path, "UPDATE users SET name = %s WHERE id = %s", ("Nytt adminnamn", admin[0]))
    events = _snapshot(all_db_path, booking_id)[2][-3:]
    assert events[0]["actor_name"] == admin[1]
    assert events[0]["actor_current_name"] == "Nytt adminnamn"
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == []


def test_fake_customer_event_with_missing_account_is_attributed_to_system(all_db_path, demo_accounts):
    admin, customer, _ = demo_accounts
    booking_id = _new_booking(all_db_path, customer, "accepted")
    _sql(all_db_path, "DELETE FROM users WHERE id = %s", (customer[0],))
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == [booking_id]
    event = _snapshot(all_db_path, booking_id)[2][-2]
    assert (event["actor"], event["actor_id"], event["actor_name"]) == ("system", None, None)
    assert event["note"].startswith("Exempeldata: ")


def test_history_seeding_never_sends_mail_or_calls_company_api(all_db_path, demo_accounts, monkeypatch):
    admin, customer, _ = demo_accounts
    booking_id = _new_booking(all_db_path, customer)
    external = Mock(side_effect=AssertionError("Exempelhistorik får inte göra externa anrop"))
    monkeypatch.setenv("send_mail", "1")
    monkeypatch.setattr(gen_func, "SMTP", external)
    monkeypatch.setattr(gen_func, "send_booked_mail", external)
    monkeypatch.setattr(gen_func, "send_proposal_mail", external)
    monkeypatch.setattr(company_lookup, "_fetch_company_name", external)
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == [booking_id]
    external.assert_not_called()


@pytest.mark.parametrize("admin_state", ["missing", "user"])
def test_missing_or_non_admin_account_does_not_claim_seed_marker(
    all_db_path, demo_accounts, admin_state,
):
    admin, customer, _ = demo_accounts
    booking_id = _new_booking(all_db_path, customer)
    before = _snapshot(all_db_path, booking_id)
    email = "missing-dev-history-admin@example.test" if admin_state == "missing" else customer[3]
    assert not _seed_table_exists(all_db_path)
    assert database.ensure_dev_booking_history(email, all_db_path) == []
    assert not _seed_table_exists(all_db_path)
    assert _snapshot(all_db_path, booking_id) == before
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == [booking_id]


def test_failed_seed_rolls_back_all_bookings_and_markers_and_allows_retry(
    all_db_path, demo_accounts, monkeypatch,
):
    admin, customer, other = demo_accounts
    booking_ids = [_new_booking(all_db_path, customer), _new_booking(all_db_path, other)]
    before = {booking_id: _snapshot(all_db_path, booking_id) for booking_id in booking_ids}
    record_history = database._record_history
    calls = 0

    def fail_second_booking(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("Exempelhistoriken kunde inte lagras")
        return record_history(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(database, "_record_history", fail_second_booking)
        with pytest.raises(RuntimeError, match="Exempelhistoriken"):
            database.ensure_dev_booking_history(admin[3], all_db_path)
    assert calls == 4
    assert not _seed_table_exists(all_db_path)
    assert {booking_id: _snapshot(all_db_path, booking_id) for booking_id in booking_ids} == before
    assert set(database.ensure_dev_booking_history(admin[3], all_db_path)) == set(booking_ids)
    assert database.ensure_dev_booking_history(admin[3], all_db_path) == []


def test_simultaneous_starts_seed_each_booking_only_once(all_db_path, demo_accounts):
    admin, customer, other = demo_accounts
    booking_ids = [_new_booking(all_db_path, customer), _new_booking(all_db_path, other)]
    barrier = Barrier(3)

    def seed():
        barrier.wait(timeout=15)
        return database.ensure_dev_booking_history(admin[3], all_db_path)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: seed(), range(3)))
    assert sorted(booking_id for result in results for booking_id in result) == sorted(booking_ids)
    assert sum(bool(result) for result in results) == 1
    assert all(len(_snapshot(all_db_path, booking_id)[2]) == 4 for booking_id in booking_ids)


def test_regular_schema_initialization_does_not_create_history_seed_table(all_db_path):
    database.create_database(all_db_path)
    assert not _seed_table_exists(all_db_path)
