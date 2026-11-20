"""Utvecklingsexempel ska gå att använda genom de riktiga bokningsflödena."""

from collections import Counter
from datetime import date, datetime
from unittest.mock import Mock

import pytest

import company_lookup
import database
import gen_func


SCENARIO_STATUSES = {
    "registered": "registered",
    "accept_proposal": "awaiting_response",
    "request_new_date": "awaiting_response",
    "cancel_proposal": "awaiting_response",
    "failed_delivery": "awaiting_response",
    "new_date_requested": "new_date_requested",
    "second_proposal": "awaiting_response",
    "accepted": "accepted",
    "rescheduled": "rescheduled",
    "completed": "completed",
    "cancelled_customer": "cancelled",
    "cancelled_admin": "cancelled",
}


@pytest.fixture
def workflow_accounts(all_db_path):
    admin = database.create_user(
        "Exempeladministratör Åsa", "workflow-admin@example.test", "test-admin-password",
        "5560747551", "admin", all_db_path,
    )
    customer = database.create_user(
        "Exempelkund Bo", "workflow-customer@example.test", "test-customer-password",
        "5560125790", "user", all_db_path,
    )
    return admin, customer


def _seed(path, accounts):
    admin, customer = accounts
    return database.ensure_dev_workflow_bookings(customer[3], admin[3], path)


def _scenarios(path, customer):
    with database._cursor(path) as cursor:
        database._execute(cursor,
                          "SELECT scenario, booking_id FROM dev_booking_scenario_seeds "
                          "WHERE user_id = %s", (customer[0],))
        markers = cursor.fetchall()
    return {scenario: database.get_booking_details_by_id(booking_id, path)
            for scenario, booking_id in markers}


def _snapshot(path):
    return {
        row[0]: (
            database.get_booking_details_by_id(row[0], path),
            database.get_booking_proposals(row[0], path),
            database.get_booking_history(row[0], path, include_actor_details=True),
        )
        for row in database.get_all_bookings(path)
    }


def _client(app, account):
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(user_id=account[0], user_name=account[1], user_email=account[3])
    return client


def test_workflow_examples_cover_actions_with_real_proposals_without_external_calls(
    all_db_path, workflow_accounts, monkeypatch,
):
    external = Mock(side_effect=AssertionError("Utvecklingsexempel får inte göra externa anrop"))
    monkeypatch.setattr(gen_func, "SMTP", external)
    monkeypatch.setattr(gen_func, "send_booked_mail", external)
    monkeypatch.setattr(company_lookup, "_fetch_company_name", external)
    admin, customer = workflow_accounts

    created = _seed(all_db_path, workflow_accounts)

    scenarios = _scenarios(all_db_path, customer)
    assert set(scenarios) == set(SCENARIO_STATUSES)
    assert len(created) == len(set(created)) == 12
    assert {booking["id"] for booking in scenarios.values()} == set(created)
    assert Counter(booking["status"] for booking in scenarios.values()) == Counter(SCENARIO_STATUSES.values())
    for scenario, booking in scenarios.items():
        assert booking["status"] == SCENARIO_STATUSES[scenario]
        assert booking["name"] == customer[1]
        assert booking["email"] == customer[3]
        assert booking["utbildning"].startswith("Exempel: ")
        assert booking["utbildningskalla"] == "fritext"
        assert isinstance(booking["antal"], int) and booking["antal"] > 0
        proposals = database.get_booking_proposals(booking["id"], all_db_path)
        pending = [proposal for proposal in proposals if proposal["state"] == "pending"]
        assert len(pending) == int(booking["status"] == "awaiting_response")
        for proposal in proposals:
            assert proposal["booking_id"] == booking["id"]
            assert date.fromisoformat(proposal["proposed_date"])
            assert proposal["proposed_city"] and proposal["proposed_venue"]
            assert datetime.fromisoformat(proposal["created_at"])
            assert proposal["delivery_status"] == ("failed" if scenario == "failed_delivery" else "disabled")
            assert proposal["sent_at"] is None
        history = database.get_booking_history(booking["id"], all_db_path, include_actor_details=True)
        assert any(event["note"].startswith("Exempeldata:") and not event["is_private"]
                   for event in history)
        assert any("prova själv" in event["note"] and not event["is_private"] for event in history)
        assert any(event["is_private"] and event["actor_id"] == admin[0] for event in history)
        assert all(datetime.fromisoformat(event["created_at"]) for event in history)
        if booking["status"] in {"accepted", "rescheduled", "completed"}:
            accepted = [proposal for proposal in proposals if proposal["state"] == "accepted"]
            assert len(accepted) == 1
            proposal = accepted[0]
            assert proposal["response"] == "accept" and proposal["responded_at"]
            assert (booking["faststallt_datum"], booking["faststalld_ort"], booking["faststalld_lokal"]) == (
                proposal["proposed_date"], proposal["proposed_city"], proposal["proposed_venue"],
            )
        else:
            assert booking["faststallt_datum"] is None
            assert booking["faststalld_ort"] is None
            assert booking["faststalld_lokal"] is None
    assert date.fromisoformat(scenarios["completed"]["faststallt_datum"]) < date.today()
    for scenario in ("new_date_requested", "second_proposal"):
        proposals = database.get_booking_proposals(scenarios[scenario]["id"], all_db_path)
        assert proposals[0]["state"] == "declined"
        assert proposals[0]["response"] == "new_date"
        assert proposals[0]["responded_at"]
        assert len(proposals) == (2 if scenario == "second_proposal" else 1)
    for scenario in ("cancelled_customer", "cancelled_admin"):
        proposal, = database.get_booking_proposals(scenarios[scenario]["id"], all_db_path)
        assert proposal["state"] == "cancelled"
        if scenario == "cancelled_customer":
            assert proposal["response"] == "cancel" and proposal["responded_at"]
        else:
            assert proposal["response"] is None and proposal["responded_at"] is None
    external.assert_not_called()


def test_workflow_examples_upgrade_existing_development_data_once_without_reset(
    all_db_path, workflow_accounts,
):
    admin, customer = workflow_accounts
    old_ids = database.ensure_dev_bookings(customer[3], all_db_path)
    database.ensure_dev_booking_history(admin[3], all_db_path)
    old = _snapshot(all_db_path)

    new_ids = _seed(all_db_path, workflow_accounts)

    assert len(new_ids) == 12 and set(new_ids).isdisjoint(old_ids)
    assert {booking_id: value for booking_id, value in _snapshot(all_db_path).items()
            if booking_id in old_ids} == old
    scenarios = _scenarios(all_db_path, customer)
    changed = scenarios["accept_proposal"]
    proposal, = database.get_booking_proposals(changed["id"], all_db_path)
    assert database.respond_to_proposal(
        changed["id"], proposal["id"], customer[3], "accept", all_db_path, actor_id=customer[0],
    )
    database.remove_booking(scenarios["registered"]["id"], all_db_path)
    before_restart = _snapshot(all_db_path)

    assert _seed(all_db_path, workflow_accounts) == []
    assert _snapshot(all_db_path) == before_restart


@pytest.mark.parametrize("customer_role, admin_role", (
    (None, "admin"), ("admin", "admin"), ("user", None), ("user", "user"),
))
def test_workflow_examples_require_correct_existing_account_roles(all_db_path, customer_role, admin_role):
    if customer_role is not None:
        database.create_user("Fel roll", "customer@example.test", "test-password",
                             "5560125790", customer_role, all_db_path)
    if admin_role is not None:
        database.create_user("Fel adminroll", "admin@example.test", "test-password",
                             "5560747551", admin_role, all_db_path)
    assert database.ensure_dev_workflow_bookings(
        "customer@example.test", "admin@example.test", all_db_path,
    ) == []
    assert database.get_all_bookings(all_db_path) == []


def test_workflow_seed_failure_rolls_back_all_data_and_can_retry(
    all_db_path, workflow_accounts, monkeypatch,
):
    insert = database._insert_booking
    calls = 0

    def fail_second_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Simulerat seedfel")
        return insert(*args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(database, "_insert_booking", fail_second_insert)
        with pytest.raises(RuntimeError, match="Simulerat seedfel"):
            _seed(all_db_path, workflow_accounts)
    assert database.get_all_bookings(all_db_path) == []
    assert len(_seed(all_db_path, workflow_accounts)) == 12


@pytest.mark.parametrize("scenario, action, target", (
    ("accept_proposal", "accept", "accepted"),
    ("request_new_date", "new_date", "new_date_requested"),
    ("cancel_proposal", "cancel", "cancelled"),
))
def test_seeded_proposals_support_customer_actions_with_owner_checks(
    scenario_app, all_db_path, workflow_accounts, csrf_from_html, scenario, action, target,
):
    admin, customer = workflow_accounts
    _seed(all_db_path, workflow_accounts)
    booking = _scenarios(all_db_path, customer)[scenario]
    proposal, = database.get_booking_proposals(booking["id"], all_db_path)
    detail = f"/bookings/{booking['id']}"
    respond = f"{detail}/proposals/{proposal['id']}/respond"
    other = database.create_user("Annan kund", "other-workflow@example.test", "test-password",
                                 "5560427220", "user", all_db_path)
    stranger = _client(scenario_app, other)
    assert stranger.get(detail).status_code == 404
    token = csrf_from_html(stranger.get("/booking").get_data(as_text=True))
    assert stranger.post(respond, data={"csrf_token": token, "action": action}).status_code == 404
    owner = _client(scenario_app, customer)
    html = owner.get(detail).get_data(as_text=True)
    assert "prova själv" in html
    assert "Acceptera förslaget" in html and "Begär nytt datum" in html and "Avbryt bokningen" in html
    private_notes = [event["note"] for event in database.get_booking_history(booking["id"], all_db_path)
                     if event["is_private"]]
    assert private_notes and all(note not in html for note in private_notes)
    response = owner.post(respond, data={"csrf_token": csrf_from_html(html), "action": action})
    assert response.status_code == 302
    assert database.get_booking_details_by_id(booking["id"], all_db_path)["status"] == target


def test_seeded_accepted_booking_supports_admin_status_and_private_notes(
    scenario_app, all_db_path, workflow_accounts, csrf_from_html,
):
    admin, customer = workflow_accounts
    _seed(all_db_path, workflow_accounts)
    booking = _scenarios(all_db_path, customer)["accepted"]
    detail = f"/admin/bookings/{booking['id']}"
    client = _client(scenario_app, admin)
    html = client.get(detail).get_data(as_text=True)
    assert "Markera som slutförd" in html
    token = csrf_from_html(html)
    response = client.post(f"{detail}/status", data={"csrf_token": token, "target": "completed"})
    assert response.status_code == 302
    confirmed = database.get_booking_details_by_id(booking["id"], all_db_path)
    assert confirmed["status"] == "completed"
    assert (confirmed["faststallt_datum"], confirmed["faststalld_ort"], confirmed["faststalld_lokal"]) == (
        booking["faststallt_datum"], booking["faststalld_ort"], booking["faststalld_lokal"],
    )
    html = client.get(detail).get_data(as_text=True)
    private_note = "En ny intern anteckning på exempelbokningen."
    response = client.post(f"{detail}/notes", data={
        "csrf_token": csrf_from_html(html), "note": private_note,
    })
    assert response.status_code == 302
    assert private_note in client.get(detail).get_data(as_text=True)
    assert private_note not in _client(scenario_app, customer).get(
        f"/bookings/{booking['id']}",
    ).get_data(as_text=True)
