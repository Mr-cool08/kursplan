"""Statusmejl följer lyckade bokningsbeslut i båda databaslägena."""
from unittest.mock import Mock

from flask import get_flashed_messages
import pytest

import database
import gen_func
from users.routes import notify_booking_status


CUSTOMER_EMAIL = "status-customer@example.test"
ADMIN_EMAIL = "status-admin@example.test"
OTHER_EMAIL = "status-other@example.test"


@pytest.fixture
def notifications(scenario_app, all_db_path, monkeypatch, csrf_from_html):
    clients = {}
    for email, role in ((CUSTOMER_EMAIL, "user"), (ADMIN_EMAIL, "admin"),
                        (OTHER_EMAIL, "user")):
        user = database.create_user("Statuskund Åsa", email, "status-test-password",
                                    "5566778899", role, all_db_path)
        client = scenario_app.test_client()
        with client.session_transaction() as state:
            state.update(user_id=user[0], user_name=user[1], user_email=email, role=role)
        clients[email] = client

    booking_id = database.create_booking_request(
        name="Statuskund Åsa", email=CUSTOMER_EMAIL, organization_number="5566778899",
        utbildning="Anpassad HLR", utbildningskalla="fritext", antal=6, datumtyp="intervall",
        onskat_datum=None, onskat_startdatum="2030-05-01", onskat_slutdatum="2030-05-09",
        onskad_ort="Önskad ort", lokalansvar="kund", lokaladress="Önskad lokal",
        db_path=all_db_path,
    )
    sender = Mock(return_value="sent")
    monkeypatch.setattr(gen_func, "send_booking_status_mail", sender)
    smtp = Mock(side_effect=AssertionError("Riktigt mejlutskick i statustest"))
    monkeypatch.setattr(gen_func, "SMTP", smtp)

    def token(client):
        response = client.get("/booking")
        assert response.status_code == 200
        return csrf_from_html(response.get_data(as_text=True))

    yield {
        "app": scenario_app, "db": all_db_path, "booking_id": booking_id,
        "customer": clients[CUSTOMER_EMAIL], "admin": clients[ADMIN_EMAIL],
        "other": clients[OTHER_EMAIL], "sender": sender, "token": token,
    }
    smtp.assert_not_called()


def _propose(case):
    proposal = database.create_proposal(
        case["booking_id"], "2030-05-06", "Fastställd ort", "Fastställd lokal Å",
        case["db"],
    )
    assert proposal is not None
    return proposal


def _post_response(case, proposal, action, *, client=None, csrf=True):
    client = client or case["customer"]
    data = {"action": action}
    if csrf:
        data["csrf_token"] = case["token"](client)
    return client.post(
        f"/bookings/{case['booking_id']}/proposals/{proposal['id']}/respond", data=data,
    )


def _post_cancel(case, channel):
    client = case["admin"] if channel.startswith("admin") else case["customer"]
    endpoint = f"/cancel_booking/{case['booking_id']}"
    data = {"csrf_token": case["token"](client)}
    if channel == "admin-status":
        endpoint = f"/admin/bookings/{case['booking_id']}/status"
        data["target"] = "cancelled"
    return client.post(endpoint, data=data)


def _assert_customer_mail(case, status):
    case["sender"].assert_called_once()
    booking, target, booking_url = case["sender"].call_args.args
    assert target == status
    assert booking["id"] == case["booking_id"]
    assert booking["email"] == CUSTOMER_EMAIL
    assert booking["utbildning"] == "Anpassad HLR"
    assert booking["antal"] == 6
    assert booking["status"] == status
    assert booking_url == f"https://kursplan.se/bookings/{case['booking_id']}"
    return booking


def test_acceptance_emails_confirmed_details_once_and_not_requested_values(notifications):
    case = notifications
    proposal = _propose(case)
    assert _post_response(case, proposal, "accept").status_code == 302
    booking = _assert_customer_mail(case, "accepted")
    assert (booking["faststallt_datum"], booking["faststalld_ort"], booking["faststalld_lokal"]) == (
        "2030-05-06", "Fastställd ort", "Fastställd lokal Å",
    )
    assert _post_response(case, proposal, "accept").status_code == 409
    assert case["sender"].call_count == 1


@pytest.mark.parametrize("channel", ("proposal", "customer", "admin-cancel", "admin-status"))
def test_each_cancellation_path_emails_the_customer_once(notifications, channel):
    case = notifications
    proposal = _propose(case)
    if channel == "proposal":
        response = _post_response(case, proposal, "cancel")
    else:
        response = _post_cancel(case, channel)
    assert response.status_code == 302
    _assert_customer_mail(case, "cancelled")
    assert database.get_booking_details_by_id(case["booking_id"], case["db"])["status"] == "cancelled"

    if channel == "proposal":
        assert _post_response(case, proposal, "cancel").status_code == 409
    else:
        assert _post_cancel(case, channel).status_code in {302, 409}
    assert case["sender"].call_count == 1


@pytest.mark.parametrize("rejection", (
    "other-accept", "other-cancel", "csrf-accept", "csrf-cancel",
    "invalid-action", "direct-admin-accept", "invalid-admin-transition",
))
def test_rejected_actions_never_email_or_change_booking(notifications, rejection):
    case = notifications
    proposal = _propose(case)
    if rejection == "other-accept":
        response = _post_response(case, proposal, "accept", client=case["other"])
        assert response.status_code == 404
    elif rejection == "other-cancel":
        response = case["other"].post(f"/cancel_booking/{case['booking_id']}", data={
            "csrf_token": case["token"](case["other"]),
        })
        assert response.status_code == 302
    elif rejection == "csrf-accept":
        assert _post_response(case, proposal, "accept", csrf=False).status_code == 400
    elif rejection == "csrf-cancel":
        assert case["customer"].post(f"/cancel_booking/{case['booking_id']}").status_code == 400
    elif rejection == "invalid-action":
        assert _post_response(case, proposal, "invalid").status_code == 400
    else:
        target = "accepted" if rejection == "direct-admin-accept" else "completed"
        assert case["admin"].post(f"/admin/bookings/{case['booking_id']}/status", data={
            "csrf_token": case["token"](case["admin"]), "target": target,
        }).status_code == 409
    assert database.get_booking_details_by_id(case["booking_id"], case["db"])["status"] == "awaiting_response"
    case["sender"].assert_not_called()


@pytest.mark.parametrize("action", ("accept", "cancel"))
@pytest.mark.parametrize("delivery", ("failed", "disabled", "exception"))
def test_mail_failure_or_disabled_delivery_preserves_customer_decision(notifications, action, delivery):
    case = notifications
    proposal = _propose(case)
    if delivery == "exception":
        case["sender"].side_effect = OSError("Testfel från mejlserver")
    else:
        case["sender"].return_value = delivery
    response = _post_response(case, proposal, action)
    assert response.status_code == 302
    expected = "accepted" if action == "accept" else "cancelled"
    saved = database.get_booking_details_by_id(case["booking_id"], case["db"])
    assert saved["status"] == expected
    _assert_customer_mail(case, expected)
    if action == "accept":
        assert saved["faststallt_datum"] == "2030-05-06"
    with case["customer"].session_transaction() as state:
        assert any(category in {"warning", "error"} for category, _ in state.get("_flashes", []))


def test_request_for_new_date_sends_no_acceptance_or_cancellation_mail(notifications):
    case = notifications
    proposal = _propose(case)
    assert _post_response(case, proposal, "new_date").status_code == 302
    assert database.get_booking_details_by_id(case["booking_id"], case["db"])["status"] == "new_date_requested"
    case["sender"].assert_not_called()


def test_admin_cancellation_mail_failure_preserves_status_and_warns_admin(notifications):
    case = notifications
    _propose(case)
    case["sender"].return_value = "failed"
    assert _post_cancel(case, "admin-status").status_code == 302
    assert database.get_booking_details_by_id(case["booking_id"], case["db"])["status"] == "cancelled"
    _assert_customer_mail(case, "cancelled")
    with case["admin"].session_transaction() as state:
        assert any(category in {"warning", "error"} for category, _ in state.get("_flashes", []))


def test_notification_lookup_failure_warns_without_attempting_delivery(scenario_app, monkeypatch):
    sender = Mock()
    monkeypatch.setattr(gen_func, "send_booking_status_mail", sender)
    monkeypatch.setattr(database, "get_booking_details_by_id", Mock(side_effect=OSError("Tillfälligt databasfel")))
    with scenario_app.test_request_context("/"):
        notify_booking_status(42, "accepted")
        assert (
            "warning", "Bokningens status är sparad, men bekräftelsen kunde inte skickas via e-post.",
        ) in get_flashed_messages(with_categories=True)
    sender.assert_not_called()
