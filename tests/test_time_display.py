"""Svensk tidsvisning utan att ändra de tidsstämplar som lagras i UTC."""

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

import database
from time_display import format_swedish_time


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-01-15T10:15:30+00:00", "2026-01-15 11:15:30 CET"),
    ("2026-07-15T10:15:30+00:00", "2026-07-15 12:15:30 CEST"),
    ("2026-03-29T00:59:59Z", "2026-03-29 01:59:59 CET"),
    ("2026-03-29T01:00:00Z", "2026-03-29 03:00:00 CEST"),
    ("2026-10-25T00:59:59Z", "2026-10-25 02:59:59 CEST"),
    ("2026-10-25T01:00:00Z", "2026-10-25 02:00:00 CET"),
    ("2026-07-15T23:30:00+00:00", "2026-07-16 01:30:00 CEST"),
    ("2026-12-31T23:30:00+00:00", "2027-01-01 00:30:00 CET"),
    ("2026-07-15T08:15:30-04:00", "2026-07-15 14:15:30 CEST"),
    ("2026-01-15T10:15:30+05:30", "2026-01-15 05:45:30 CET"),
    ("2026-01-15 10:15:30", "2026-01-15 11:15:30 CET"),
    ("2026-07-15T10:15:30", "2026-07-15 12:15:30 CEST"),
    (datetime(2026, 1, 15, 10, 15, 30, tzinfo=timezone.utc),
     "2026-01-15 11:15:30 CET"),
    (datetime(2026, 7, 15, 8, 15, 30, tzinfo=timezone(timedelta(hours=-4))),
     "2026-07-15 14:15:30 CEST"),
    (datetime(2026, 7, 15, 10, 15, 30), "2026-07-15 12:15:30 CEST"),
])
def test_swedish_time_observes_offsets_calendar_rollovers_and_daylight_saving(value, expected):
    assert format_swedish_time(value) == expected


@pytest.mark.parametrize("value", [
    None, "", "   ", "ogiltig tid", "2026-02-30T10:00:00+00:00",
    "<script>alert('tid')</script>", 0, True, [], {}, date(2026, 1, 15),
])
def test_missing_or_invalid_timestamp_has_a_swedish_fallback(value):
    assert format_swedish_time(value) == "Tid saknas"


class _RenderedPage(HTMLParser):
    """Keep visible strings separate from machine-readable datetime attributes."""

    def __init__(self, html):
        super().__init__()
        self.parts = []
        self.time_texts = []
        self._time_parts = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "time":
            self._time_parts = []

    def handle_data(self, data):
        self.parts.append(data)
        if self._time_parts is not None:
            self._time_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "time" and self._time_parts is not None:
            self.time_texts.append("".join(self._time_parts).strip())
            self._time_parts = None

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


def _signed_client(app, user):
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(user_id=user[0], user_name=user[1], user_email=user[3])
    return client


def _read_page(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return _RenderedPage(response.get_data(as_text=True))


def _stored_times(booking_id, db_path):
    proposals = database.get_booking_proposals(booking_id, db_path)
    history = database.get_booking_history(booking_id, db_path)
    return (
        [(p["created_at"], p["sent_at"], p["responded_at"]) for p in proposals],
        [(event["id"], event["created_at"]) for event in history],
    )


@pytest.mark.parametrize(("utc_day", "swedish_day", "local_hour", "zone"), [
    ("2026-12-31", "2027-01-01", "00", "CET"),
    ("2026-07-15", "2026-07-16", "01", "CEST"),
], ids=["winter-and-new-year", "summer-and-next-day"])
def test_customer_admin_and_audit_log_show_swedish_time_and_preserve_utc(
    scenario_app, all_db_path, monkeypatch, utc_day, swedish_day, local_hour, zone,
):
    customer = database.create_user(
        "Tidskund", "time-customer@example.test", "time-test-password",
        "5566778899", "user", all_db_path,
    )
    administrator = database.create_user(
        "Tidsadministratör", "time-admin@example.test", "time-test-password",
        "5566778899", "admin", all_db_path,
    )
    utc_times = {key: f"{utc_day}T23:{minute}:00+00:00" for key, minute in (
        ("booking", "30"), ("created", "31"), ("sent", "32"), ("responded", "33"),
    )}
    local_times = {key: f"{swedish_day} {local_hour}:{minute}:00 {zone}" for key, minute in (
        ("booking", "30"), ("created", "31"), ("sent", "32"), ("responded", "33"),
    )}
    clock = {"now": utc_times["booking"]}
    monkeypatch.setattr(database, "_now", lambda: clock["now"])
    booking_id = database.create_booking(
        customer[1], customer[3], customer[2], "HLR", 5, "Örebro", "Sal 1",
        "2030-08-20", all_db_path,
    )
    clock["now"] = utc_times["created"]
    proposal = database.create_proposal(
        booking_id, "2030-08-24", "Växjö", "Sal 2", all_db_path,
        actor_id=administrator[0],
    )
    clock["now"] = utc_times["sent"]
    # Record a successful delivery without invoking SMTP or sending real mail.
    assert database.claim_proposal_delivery(proposal["id"], all_db_path, actor_id=administrator[0])
    assert database.finish_proposal_delivery(proposal["id"], "sent", all_db_path)
    customer_client = _signed_client(scenario_app, customer)
    admin_client = _signed_client(scenario_app, administrator)
    detail_paths = (
        (customer_client, f"/bookings/{booking_id}"),
        (admin_client, f"/admin/bookings/{booking_id}"),
    )
    before_reads = _stored_times(booking_id, all_db_path)
    assert before_reads[0] == [(utc_times["created"], utc_times["sent"], None)]
    for client, path in detail_paths:
        page = _read_page(client, path)
        assert f"Skapat {local_times['created']}" in page.text
        # Administrators also see delivery time in the active-proposal summary.
        expected_deliveries = 2 if client is admin_client else 1
        assert page.text.count(f"E-post skickad {local_times['sent']}") == expected_deliveries
        assert local_times["booking"] in page.time_texts
        assert local_times["created"] in page.time_texts
        assert "2030-08-20" in page.text and "2030-08-24" in page.text
        assert " UTC" not in page.text and "+00:00" not in page.text
    assert _stored_times(booking_id, all_db_path) == before_reads

    clock["now"] = utc_times["responded"]
    assert database.respond_to_proposal(
        booking_id, proposal["id"], customer[3], "accept", all_db_path, actor_id=customer[0],
    )
    before_reads = _stored_times(booking_id, all_db_path)
    assert before_reads[0] == [(
        utc_times["created"], utc_times["sent"], utc_times["responded"],
    )]
    for client, path in detail_paths:
        page = _read_page(client, path)
        assert f"Skapat {local_times['created']}" in page.text
        assert f"E-post skickad {local_times['sent']}" in page.text
        assert f"Kundens svar: Accepterat · {local_times['responded']}" in page.text
        assert local_times["responded"] in page.time_texts
        assert " UTC" not in page.text and "+00:00" not in page.text
    audit_page = _read_page(admin_client, "/admin/logs")
    assert set(local_times.values()).issubset(audit_page.time_texts)
    assert " UTC" not in audit_page.text and "+00:00" not in audit_page.text
    assert _stored_times(booking_id, all_db_path) == before_reads
    assert all(stamp.endswith("+00:00") for _, stamp in before_reads[1])
