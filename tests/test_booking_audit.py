"""Beständig och behörighetsskyddad historik för bokningsåtgärder."""
from datetime import datetime
import re

import pytest

import database
import gen_func


PASSWORD = "audit-test-password"


def _signed_client(app, user):
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(user_id=user[0], user_name="Förfalskat sessionsnamn",
                     user_email=user[3], role="admin")
    return client


def _history(booking_id, db_path):
    return database.get_booking_history(booking_id, db_path, include_actor_details=True)


def _sql(db_path, query, params=()):
    with database._cursor(db_path) as cursor:
        database._execute(cursor, query, params)


@pytest.fixture
def audit_world(scenario_app, all_db_path):
    first = database.create_user("Admin Åsa", "audit-first@example.test", PASSWORD,
                                 "5566778899", "admin", all_db_path)
    second = database.create_user("Admin Bo", "audit-second@example.test", PASSWORD,
                                  "5566778899", "admin", all_db_path)
    customer = database.create_user("Kund Cecilia", "audit-customer@example.test", PASSWORD,
                                    "5566778899", "user", all_db_path)
    booking_id = database.create_booking(
        customer[1], customer[3], customer[2], "HLR", 5, "Örebro", "Sal 1",
        "2030-08-20", all_db_path,
    )
    return scenario_app, all_db_path, first, second, customer, booking_id


def test_actions_keep_the_actual_account_and_status_at_each_step(audit_world):
    _, path, first, second, customer, booking_id = audit_world
    assert database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    proposal = database.create_proposal(booking_id, "2030-08-24", "Växjö", "Sal 2", path,
                                         actor_id=second[0])
    assert database.respond_to_proposal(booking_id, proposal["id"], customer[3], "accept", path,
                                        actor_id=customer[0])
    assert database.add_booking_note(booking_id, "Ring kunden inför kursen.", path, actor_id=first[0])
    assert database.transition_booking(booking_id, "completed", path, actor_id=second[0])
    history = _history(booking_id, path)
    assert [(event["actor"], event["actor_id"], event["actor_name"]) for event in history] == [
        ("customer", customer[0], customer[1]),
        ("admin", first[0], first[1]),
        ("admin", second[0], second[1]),
        ("customer", customer[0], customer[1]),
        ("admin", first[0], first[1]),
        ("admin", second[0], second[1]),
    ]
    assert history[3]["from_status"] == "awaiting_response"
    assert history[3]["to_status"] == "accepted"
    assert history[4]["kind"] == "note" and history[4]["is_private"]
    assert all(datetime.fromisoformat(event["created_at"]).utcoffset() is not None for event in history)


def test_actor_snapshot_survives_a_renamed_and_deleted_account(audit_world):
    _, path, first, _, _, booking_id = audit_world
    assert database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    original = _history(booking_id, path)[-1]
    _sql(path, "UPDATE users SET name = %s WHERE id = %s", ("Nytt kontonamn", first[0]))
    renamed = _history(booking_id, path)[-1]
    assert renamed["actor_name"] == first[1]
    assert renamed["actor_current_name"] == "Nytt kontonamn"
    _sql(path, "DELETE FROM users WHERE id = %s", (first[0],))
    deleted = _history(booking_id, path)[-1]
    for field in ("id", "actor_id", "actor_name", "created_at", "note", "from_status", "to_status"):
        assert deleted[field] == original[field]
    assert deleted["actor_current_name"] is None
    assert database.list_booking_audit_log(path)[0]["actor_name"] == first[1]


def test_rejected_and_repeated_actions_do_not_fabricate_audit_events(audit_world):
    _, path, first, _, customer, booking_id = audit_world
    before = _history(booking_id, path)
    assert not database.transition_booking(booking_id, "accepted", path, actor_id=first[0])
    assert not database.transition_booking(booking_id, "cancelled", path, actor="customer",
                                           email="someone-else@example.test", actor_id=customer[0])
    assert _history(booking_id, path) == before
    assert database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    registered = _history(booking_id, path)
    assert not database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    assert _history(booking_id, path) == registered


def test_status_change_rolls_back_when_history_cannot_be_saved(audit_world, monkeypatch):
    _, path, first, _, _, booking_id = audit_world
    before = _history(booking_id, path)
    execute = database._execute

    def fail_audit_insert(cursor, query, params=()):
        if query.lstrip().startswith("INSERT INTO booking_history"):
            raise RuntimeError("Simulerat fel vid historiklagring")
        return execute(cursor, query, params)

    monkeypatch.setattr(database, "_execute", fail_audit_insert)
    with pytest.raises(RuntimeError, match="historiklagring"):
        database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    assert database.get_booking_details_by_id(booking_id, path)["status"] == "pending"
    assert _history(booking_id, path) == before


def test_delivery_audit_distinguishes_initiator_from_system_result(audit_world):
    _, path, first, second, _, booking_id = audit_world
    proposal = database.create_proposal(booking_id, "2030-08-24", "Växjö", "Sal 2", path,
                                         actor_id=first[0])
    assert database.claim_proposal_delivery(proposal["id"], path, actor_id=second[0])
    started = _history(booking_id, path)[-1]
    assert (started["kind"], started["actor"], started["actor_id"], started["actor_name"]) == (
        "delivery", "admin", second[0], second[1],
    )
    assert started["is_private"]
    visible = database.get_booking_history(booking_id, path, include_private=False,
                                            include_actor_details=True)
    assert started["id"] not in {event["id"] for event in visible}
    assert not database.claim_proposal_delivery(proposal["id"], path, actor_id=first[0])
    assert _history(booking_id, path)[-1]["id"] == started["id"]
    assert database.finish_proposal_delivery(proposal["id"], "disabled", path)
    finished = _history(booking_id, path)[-1]
    assert finished["kind"] == "delivery" and finished["actor"] == "system"
    assert finished["actor_id"] is None and finished["actor_name"] is None


def test_default_history_does_not_return_account_names(audit_world):
    _, path, first, _, _, booking_id = audit_world
    assert database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    for event in database.get_booking_history(booking_id, path, include_private=False):
        assert event["actor_name"] is None
        assert event.get("actor_current_name") is None


@pytest.mark.parametrize("actor", ["anonymous", "customer", "demoted-admin"])
def test_log_overview_checks_current_database_role(audit_world, actor):
    app, path, first, _, customer, _ = audit_world
    if actor == "anonymous":
        client = app.test_client()
    elif actor == "customer":
        client = _signed_client(app, customer)
    else:
        client = _signed_client(app, first)
        _sql(path, "UPDATE users SET role = 'user' WHERE id = %s", (first[0],))
    response = client.get("/admin/logs")
    assert response.status_code == (302 if actor == "anonymous" else 403)
    assert "Kund Cecilia" not in response.get_data(as_text=True)


def test_route_records_database_identity_and_shares_only_public_events_with_customer(audit_world, csrf_from_html):
    app, path, first, second, customer, booking_id = audit_world
    admin = _signed_client(app, first)
    token = csrf_from_html(admin.get(f"/admin/bookings/{booking_id}").get_data(as_text=True))
    response = admin.post(f"/admin/bookings/{booking_id}/status", data={
        "csrf_token": token, "target": "registered", "actor_id": second[0],
        "actor_name": "Förfalskat formulärnamn",
    })
    assert response.status_code == 302
    saved = _history(booking_id, path)[-1]
    assert (saved["actor_id"], saved["actor_name"]) == (first[0], first[1])
    assert database.add_booking_note(booking_id, "Hemlig intern kommentar", path, actor_id=first[0])
    detail = admin.get(f"/admin/bookings/{booking_id}").get_data(as_text=True)
    overview = admin.get("/admin/logs")
    assert overview.status_code == 200
    assert "no-store" in overview.headers.get("Cache-Control", "")
    for html in (detail, overview.get_data(as_text=True)):
        assert first[1] in html
        assert "Hemlig intern kommentar" in html
        assert "Förfalskat" not in html
    customer_html = _signed_client(app, customer).get(f"/bookings/{booking_id}").get_data(as_text=True)
    assert "Administratören har registrerat förfrågan" in customer_html
    assert first[1] in customer_html and "Hemlig intern kommentar" not in customer_html
    assert "konto #" not in customer_html.lower()
    assert 'href="/admin/logs"' in admin.get("/admin/").get_data(as_text=True)


@pytest.mark.parametrize("actor", ["customer", "admin"])
def test_booking_detail_shows_latest_history_first_even_with_equal_timestamps(audit_world, actor):
    app, path, first, _, customer, booking_id = audit_world
    assert database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    private_note = "Intern anteckning mellan statusändringarna."
    assert database.add_booking_note(booking_id, private_note, path, actor_id=first[0])
    assert database.transition_booking(booking_id, "cancelled", path, actor="customer",
                                       email=customer[3], actor_id=customer[0])
    _sql(path, "UPDATE booking_history SET created_at = %s WHERE booking_id = %s",
         ("2030-08-01T12:00:00+00:00", booking_id))

    user = first if actor == "admin" else customer
    prefix = "/admin" if actor == "admin" else ""
    response = _signed_client(app, user).get(f"{prefix}/bookings/{booking_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    notes = re.findall(r'<p class="history-note">(.*?)</p>', html)
    expected = ["Kunden har avbrutit bokningen."]
    if actor == "admin":
        expected.append(private_note)
    else:
        assert private_note not in html
    expected.extend([
        "Administratören har registrerat förfrågan.",
        "Bokningsförfrågan skapad.",
    ])
    assert notes == expected


def test_retry_route_records_the_admin_who_started_delivery(audit_world, csrf_from_html, monkeypatch):
    app, path, first, second, _, booking_id = audit_world
    proposal = database.create_proposal(booking_id, "2030-08-24", "Växjö", "Sal 2", path,
                                         actor_id=first[0])
    monkeypatch.setattr(gen_func, "send_proposal_mail", lambda *args: "disabled")
    client = _signed_client(app, second)
    token = csrf_from_html(client.get(f"/admin/bookings/{booking_id}").get_data(as_text=True))
    response = client.post(f"/admin/bookings/{booking_id}/proposals/{proposal['id']}/retry",
                           data={"csrf_token": token})
    assert response.status_code == 302
    attempts = [event for event in _history(booking_id, path)
                if event["kind"] == "delivery" and event["actor"] == "admin"]
    assert len(attempts) == 1
    assert (attempts[0]["actor_id"], attempts[0]["actor_name"]) == (second[0], second[1])


def test_actor_names_and_notes_are_escaped_in_both_admin_views(audit_world):
    app, path, first, _, _, booking_id = audit_world
    name = '<img src=x onerror="alert(1)">'
    note = '<script>alert("note")</script>'
    _sql(path, "UPDATE users SET name = %s WHERE id = %s", (name, first[0]))
    assert database.add_booking_note(booking_id, note, path, actor_id=first[0])
    client = _signed_client(app, first)
    for url in ("/admin/logs", f"/admin/bookings/{booking_id}"):
        html = client.get(url).get_data(as_text=True)
        assert name not in html and note not in html
        assert "&lt;img src=x" in html and "&lt;script&gt;" in html


def test_overview_has_stable_newest_first_pagination(audit_world):
    app, path, first, _, _, booking_id = audit_world
    with database._cursor(path) as cursor:
        for number in range(52):
            database._execute(cursor,
                "INSERT INTO booking_history (booking_id, actor, actor_id, actor_name, note, "
                "created_at, kind, is_private) VALUES (%s, 'admin', %s, %s, %s, %s, 'note', 1)",
                (booking_id, first[0], first[1], f"Auditnotis {number:02d}", "2030-08-01T12:00:00+00:00"))
    first_page = database.list_booking_audit_log(path, limit=50, offset=0)
    second_page = database.list_booking_audit_log(path, limit=50, offset=50)
    assert len(first_page) == 50 and len(second_page) == 3
    assert first_page[0]["note"] == "Auditnotis 51"
    assert first_page[-1]["note"] == "Auditnotis 02"
    assert second_page[0]["note"] == "Auditnotis 01"
    assert all(event["utbildning"] == "HLR" for event in first_page + second_page)
    assert not {event["id"] for event in first_page} & {event["id"] for event in second_page}
    client = _signed_client(app, first)
    first_html = client.get("/admin/logs").get_data(as_text=True)
    second_html = client.get("/admin/logs?page=2").get_data(as_text=True)
    assert "Auditnotis 51" in first_html and "Auditnotis 01" not in first_html
    assert "Auditnotis 01" in second_html and "Auditnotis 51" not in second_html
    assert "page=2" in first_html
    assert "page=3" not in second_html


@pytest.mark.parametrize("page", ["0", "-1", "abc", "1.5"])
def test_overview_rejects_invalid_page_numbers(audit_world, page):
    app, _, first, *_ = audit_world
    assert _signed_client(app, first).get("/admin/logs", query_string={"page": page}).status_code == 400


def test_history_migration_preserves_old_rows_without_inventing_snapshots(audit_world):
    _, path, first, _, _, booking_id = audit_world
    assert database.transition_booking(booking_id, "registered", path, actor_id=first[0])
    before = _history(booking_id, path)
    _sql(path, "ALTER TABLE booking_history DROP COLUMN actor_name")
    database.create_database(path)
    database.create_database(path)
    after = _history(booking_id, path)
    assert len(after) == len(before)
    for previous, migrated in zip(before, after):
        for field in ("id", "booking_id", "actor", "actor_id", "note", "created_at", "kind", "is_private"):
            assert migrated[field] == previous[field]
        assert migrated["actor_name"] is None
    assert after[-1]["actor_current_name"] == first[1]


def test_legacy_missing_actor_is_shown_without_a_fabricated_person(audit_world):
    app, path, first, _, _, booking_id = audit_world
    _sql(path, "UPDATE booking_history SET actor = 'system', actor_id = NULL, actor_name = NULL, "
         "kind = 'baseline', note = 'Historik från migreringen.' WHERE booking_id = %s", (booking_id,))
    entry = _history(booking_id, path)[0]
    assert entry["actor_name"] is None and entry["actor_current_name"] is None
    html = _signed_client(app, first).get("/admin/logs").get_data(as_text=True)
    assert "Historik från migreringen." in html
    assert "System" in html and "Admin Åsa" not in html
