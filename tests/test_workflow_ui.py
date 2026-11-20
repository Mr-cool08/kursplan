"""Render the workflow templates with real CSS and local-only browser requests."""

from urllib.parse import urlsplit

from flask import Flask, render_template
from playwright.sync_api import expect
import pytest

from time_display import format_swedish_time


STATUS_LABELS = {
    "pending": "Ny förfrågan", "registered": "Registrerad",
    "awaiting_response": "Inväntar kundens svar",
    "new_date_requested": "Nytt datum önskas", "accepted": "Accepterad",
    "rescheduled": "Ombokad", "completed": "Slutförd", "cancelled": "Avbruten",
}
LONG_COURSE = "Anpassad säkerhetsutbildning " + "Utbildning" * 15
LONG_VENUE = "Konferenslokalen, " + "Långadress" * 20
LONG_COMPANY = "Svenska " + "Utbildningsföretaget" * 20 + " AB"
LONG_ADMIN = "Administratör Åsa " + "LångtEfternamn" * 20


@pytest.fixture
def workflow_ui_client(project_root):
    app = Flask(
        "workflow-ui-tests", template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(TESTING=True, SECRET_KEY="workflow-ui-test-secret")
    app.add_template_filter(format_swedish_time, "swedish_time")
    paths = {
        "user_page.index": "/", "user_page.dashboard": "/dashboard",
        "user_page.booking": "/booking", "logout": "/logout",
        "user_page.booking_detail": "/bookings/<int:booking_id>",
        "user_page.cancel_booking": "/cancel_booking/<int:booking_id>",
        "user_page.respond_proposal": "/bookings/<int:booking_id>/proposals/<int:proposal_id>/respond",
        "admin_page.dashboard": "/admin/",
        "admin_page.logs": "/admin/logs",
        "admin_page.booking_detail": "/admin/bookings/<int:booking_id>",
        "admin_page.preview_proposal": "/admin/bookings/<int:booking_id>/proposal/preview",
        "admin_page.send_proposal": "/admin/bookings/<int:booking_id>/proposal",
        "admin_page.retry_delivery": "/admin/bookings/<int:booking_id>/proposals/<int:proposal_id>/retry",
        "admin_page.change_status": "/admin/bookings/<int:booking_id>/status",
        "admin_page.add_note": "/admin/bookings/<int:booking_id>/notes",
    }
    for endpoint, path in paths.items():
        app.add_url_rule(path, endpoint=endpoint, view_func=lambda **kwargs: "")
    app.add_url_rule(
        "/admin/bookings/<int:booking_id>/company", endpoint="admin_page.company_name",
        view_func=lambda booking_id: {"status": "found", "name": LONG_COMPANY},
    )

    booking = {
        "id": 42, "name": "Anna " + "LångtEfternamn" * 10,
        "email": "anna@example.com", "organization_number": "5566778899",
        "utbildning": LONG_COURSE, "antal": 12, "datumtyp": "intervall",
        "onskat_datum": None, "onskat_startdatum": "2027-03-18",
        "onskat_slutdatum": "2027-03-22", "datum": "2027-03-18 – 2027-03-22",
        "onskad_ort": None, "ort": None, "lokalansvar": "kund",
        "lokaladress": LONG_VENUE, "lokal": LONG_VENUE,
        "faststallt_datum": None, "faststalld_ort": None,
        "faststalld_lokal": None, "status": "awaiting_response",
        "latest_event": "Ett datumförslag har skickats.",
    }
    proposal = {
        "id": 3, "booking_id": 42, "proposed_date": "2027-03-20",
        "proposed_city": "Göteborg", "proposed_venue": LONG_VENUE,
        "state": "pending", "delivery_status": "failed", "sent_at": None,
        "created_at": "2026-11-20T10:00:00+00:00", "response": None,
        "responded_at": None,
    }
    context = {
        "booking": booking, "bookings": [booking], "name": booking["name"],
        "total_bookings": 1, "awaiting_response_count": 1, "selected_statuses": set(),
        "status_labels": STATUS_LABELS,
        "customer_cancellable": {"pending", "registered", "new_date_requested", "awaiting_response"},
        "csrf_token": "test-csrf-token", "confirmation_token": "signed-preview",
        "admin_targets": ["cancelled"], "active_proposal": proposal,
        "proposals": [proposal], "history": [{
            "created_at": "2026-11-20T10:00:00+00:00", "actor": "admin",
            "actor_id": 17, "actor_name": LONG_ADMIN,
            "from_status": "pending", "to_status": "awaiting_response",
            "note": "<script>alert('historik')</script>",
        }, {
            "created_at": "2026-11-20T10:00:00+00:00", "actor": "admin",
            "actor_id": 17, "actor_name": LONG_ADMIN,
            "from_status": "awaiting_response", "to_status": "awaiting_response",
            "note": "Nästa händelse i bokningen.",
        }, {
            "created_at": "2026-11-20T10:00:00+00:00", "actor": "admin",
            "actor_id": 17, "actor_name": LONG_ADMIN,
            "from_status": "awaiting_response", "to_status": "awaiting_response",
            "note": "Senaste händelsen i bokningen.",
        }],
        "proposal_values": {
            "proposed_date": proposal["proposed_date"],
            "proposed_city": proposal["proposed_city"],
            "proposed_venue": proposal["proposed_venue"],
        },
        "selected_action": "accept", "error": None,
    }

    @app.route("/render/<view>")
    def render(view):
        templates = {
            "customer": "booking_detail.html", "admin": "booking_detail.html",
            "overview": "admin_dashboard.html", "preview": "proposal_preview.html",
            "dashboard": "dashboard.html", "duplicates": "admin_dashboard.html",
            "malformed-first": "admin_dashboard.html", "valid-first": "admin_dashboard.html",
            "logs": "admin_logs.html", "logs-escaped": "admin_logs.html",
        }
        values = dict(context)
        if view in {"logs", "logs-escaped"}:
            event = {
                **context["history"][0], "booking_id": booking["id"],
                "utbildning": LONG_COURSE, "is_private": 1,
            }
            if view == "logs-escaped":
                event["actor_name"] = '<img src="invalid" onerror="window.actorInjected = true">'
            values.update(events=[event], page=2, has_next=True)
        elif view == "duplicates":
            values["bookings"] = [booking, {**booking, "id": 43, "organization_number": "556677-8899"}]
        elif view in {"malformed-first", "valid-first"}:
            values["bookings"] = [
                {**booking, "organization_number": "5560747551"},
                {**booking, "id": 43, "organization_number": "556074 7551"},
                {**booking, "id": 44, "organization_number": "5560-74-7551"},
            ]
            if view == "malformed-first":
                values["bookings"].reverse()
        return render_template(templates[view], is_admin=view == "admin", **values)

    return app.test_client()


def _open_view(page, client, view, *, company_result=None, company_status=200, company_calls=None):
    def serve_local(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/company"):
            if company_calls is not None:
                company_calls.append(path)
            if company_result is not None:
                result = company_result(path) if callable(company_result) else company_result
                route.fulfill(status=company_status, json=result)
                return
        response = client.get(path)
        route.fulfill(
            status=response.status_code, body=response.get_data(),
            content_type=response.content_type,
        )

    page.route("http://workflow.test/**", serve_local)
    page.goto(f"http://workflow.test/render/{view}")


def test_workflow_detail_and_preview_render_safe_actions(workflow_ui_client):
    customer = workflow_ui_client.get("/render/customer").get_data(as_text=True)
    assert '<span class="status-pill awaiting_response">Inväntar ditt svar</span>' in customer
    assert "Ny förfrågan &rarr; Inväntar ditt svar" in customer
    assert 'method="post"' in customer
    assert '/bookings/42/proposals/3/respond' in customer
    assert 'name="action" value="accept"' in customer
    assert 'name="action" value="new_date"' in customer
    assert 'name="action" value="cancel"' in customer
    assert "&lt;script&gt;" in customer
    assert "<script>alert('historik')</script>" not in customer
    assert "Ny administrativ anteckning" not in customer
    assert "data-company-url" not in customer
    assert "admin_companies.js" not in customer
    assert LONG_ADMIN in customer
    assert "Konto #17" not in customer
    assert "/admin/logs" not in customer
    admin = workflow_ui_client.get("/render/admin").get_data(as_text=True)
    assert '<span class="status-pill awaiting_response">Inväntar kundens svar</span>' in admin
    assert "Ny förfrågan &rarr; Inväntar kundens svar" in admin
    assert "/proposals/3/retry" in admin
    assert "E-postmeddelandet har inte skickats" in admin
    assert "Ny administrativ anteckning" in admin
    assert 'name="action" value="accept"' not in admin
    assert 'data-company-url="/admin/bookings/42/company"' in admin
    assert "admin_companies.js" in admin
    assert "Företagsnamn ej hämtat" in admin
    assert LONG_ADMIN in admin
    assert "Konto #17" in admin
    assert 'href="/admin/logs"' in admin
    preview = workflow_ui_client.get("/render/preview").get_data(as_text=True)
    assert 'name="confirmation_token" value="signed-preview"' in preview
    assert 'name="confirmed" value="1"' in preview
    assert 'action="/admin/bookings/42/proposal"' in preview

    dashboard = workflow_ui_client.get("/render/dashboard").get_data(as_text=True)
    assert '<span class="status-pill awaiting_response">Inväntar ditt svar</span>' in dashboard
    assert '<span>Inväntar ditt svar</span>' in dashboard
    assert "Inväntar kundens svar" not in dashboard
    overview = workflow_ui_client.get("/render/overview").get_data(as_text=True)
    assert '<span class="status-pill awaiting_response">Inväntar kundens svar</span>' in overview
    assert "Inväntar ditt svar" not in overview


@pytest.mark.parametrize("view", ["customer", "admin"])
@pytest.mark.parametrize("width", [320, 1280])
def test_booking_history_shows_separate_events_newest_first(page, workflow_ui_client, view, width):
    page.set_viewport_size({"width": width, "height": 844})
    _open_view(page, workflow_ui_client, view)
    events = page.locator(".booking-history > li")
    expect(events).to_have_count(3)
    expect(events.locator(".history-note")).to_have_text([
        "Senaste händelsen i bokningen.",
        "Nästa händelse i bokningen.",
        "<script>alert('historik')</script>",
    ])
    boxes = [event.bounding_box() for event in events.all()]
    assert all(boxes)
    for current, following in zip(boxes, boxes[1:]):
        assert current["y"] + current["height"] < following["y"]
    for event in events.all():
        assert event.evaluate("el => getComputedStyle(el).borderTopStyle") == "solid"
        assert event.evaluate("el => parseFloat(getComputedStyle(el).borderTopWidth)") > 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [320, 768, 1280])
@pytest.mark.parametrize("view", ["customer", "admin", "overview", "preview", "dashboard", "logs"])
def test_workflow_views_fit_mobile_and_desktop(page, workflow_ui_client, width, view):
    page.set_viewport_size({"width": width, "height": 844})
    _open_view(page, workflow_ui_client, view)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    if view in {"admin", "overview"}:
        expect(page.locator("[data-company-url]")).to_have_text(LONG_COMPANY)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        f"{view} overflows at {width}px: " + str(page.evaluate(
            "[...document.querySelectorAll('*')].filter(el => "
            "el.getBoundingClientRect().right > innerWidth || el.scrollWidth > el.clientWidth).map(el => "
            "({tag: el.tagName, cls: el.className, id: el.id, "
            "right: el.getBoundingClientRect().right, sw:el.scrollWidth, cw:el.clientWidth})).slice(0, 12)"
        ))
    )
    for button in page.get_by_role("button").all():
        box = button.bounding_box()
        assert box and box["height"] >= 44 and box["width"] >= 44
        button.focus()
        expect(button).to_be_focused()
        assert button.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
    assert page.evaluate(
        "document.querySelector('footer').getBoundingClientRect().top >= "
        "document.querySelector('main').getBoundingClientRect().bottom - 1"
    ), "Footer overlaps booking content"


def test_audit_log_displays_actor_booking_status_and_pagination(workflow_ui_client):
    html = workflow_ui_client.get("/render/logs").get_data(as_text=True)
    assert "Händelselogg" in html
    assert 'href="/admin/bookings/42"' in html
    assert "Bokning #42" in html
    assert LONG_COURSE in html
    assert LONG_ADMIN in html
    assert "Konto #17" in html
    assert "Ny förfrågan &rarr; Inväntar kundens svar" in html
    assert 'datetime="2026-11-20T10:00:00+00:00"' in html
    assert "2026-11-20 11:00:00 CET" in html
    assert "Tider visas i svensk tid" in html
    assert "Endast administratörer" in html
    assert 'href="/admin/logs?page=1"' in html
    assert 'href="/admin/logs?page=3"' in html
    assert "&lt;script&gt;" in html
    assert "<script>alert('historik')</script>" not in html


@pytest.mark.parametrize("view", ["customer", "admin", "logs"])
@pytest.mark.parametrize("visitor_timezone", ["America/Los_Angeles", "Asia/Tokyo"])
def test_booking_timestamps_use_swedish_time_in_other_browser_timezones(
    new_context, workflow_ui_client, view, visitor_timezone,
):
    page = new_context(timezone_id=visitor_timezone).new_page()
    _open_view(page, workflow_ui_client, view)
    for timestamp in page.locator("main time").all():
        expect(timestamp).to_have_text("2026-11-20 11:00:00 CET")
    assert page.locator("main time").count() > 0


def test_audit_log_escapes_actor_names_and_notes(page, workflow_ui_client):
    _open_view(page, workflow_ui_client, "logs-escaped")
    timeline = page.get_by_role("list", name="Händelser för alla bokningar")
    expect(timeline).to_contain_text('<img src="invalid" onerror="window.actorInjected = true">')
    expect(timeline).to_contain_text("<script>alert('historik')</script>")
    assert timeline.locator("img, script").count() == 0
    assert page.evaluate("window.actorInjected === undefined")


@pytest.mark.parametrize("view", ["admin", "overview"])
def test_company_name_is_displayed_as_text(page, workflow_ui_client, view):
    name = '<img src="invalid" onerror="window.companyInjected = true"> Företag & Söner AB'
    _open_view(page, workflow_ui_client, view, company_result={"status": "found", "name": name})
    company = page.locator("[data-company-url]")
    expect(company).to_have_text(name)
    expect(company).to_have_attribute("data-company-state", "found")
    assert company.locator("img").count() == 0
    assert page.evaluate("window.companyInjected === undefined")
    assert "5566778899" in page.locator("main").inner_text()


@pytest.mark.parametrize(("result", "http_status", "message"), [
    ({"status": "not_found", "name": None}, 200, "Företagsnamn hittades inte"),
    ({"status": "invalid", "name": None}, 200, "Organisationsnumret kan inte slås upp"),
    ({"status": "unavailable", "name": None}, 200, "Företagsnamnet kunde inte hämtas just nu"),
    ({"status": "found", "name": None}, 200, "Företagsnamnet kunde inte hämtas just nu"),
    ({"error": "failed"}, 503, "Företagsnamnet kunde inte hämtas just nu"),
])
def test_company_lookup_failures_keep_booking_actions_available(
    page, workflow_ui_client, result, http_status, message,
):
    _open_view(page, workflow_ui_client, "overview", company_result=result, company_status=http_status)
    expect(page.locator("[data-company-url]")).to_have_text(message)
    expect(page.get_by_role("link", name="Öppna bokning #42")).to_be_visible()
    assert "5566778899" in page.locator("main").inner_text()


def test_repeated_organization_is_looked_up_once(page, workflow_ui_client):
    calls = []
    _open_view(page, workflow_ui_client, "duplicates", company_calls=calls)
    companies = page.locator("[data-company-url]")
    expect(companies).to_have_count(2)
    expect(companies).to_have_text([LONG_COMPANY, LONG_COMPANY])
    assert len(calls) == 1


@pytest.mark.parametrize("view", ["malformed-first", "valid-first"])
def test_malformed_numbers_do_not_share_a_valid_company_result(page, workflow_ui_client, view):
    calls = []

    def company_result(path):
        if path == "/admin/bookings/42/company":
            return {"status": "found", "name": "IKEA of Sweden AB"}
        return {"status": "invalid", "name": None}

    _open_view(page, workflow_ui_client, view, company_result=company_result, company_calls=calls)
    expect(page.locator('[data-company-organization="5560747551"]')).to_have_text("IKEA of Sweden AB")
    for malformed in ("556074 7551", "5560-74-7551"):
        expect(page.locator(f'[data-company-organization="{malformed}"]')).to_have_text(
            "Organisationsnumret kan inte slås upp",
        )
    assert sorted(calls) == [f"/admin/bookings/{booking_id}/company" for booking_id in (42, 43, 44)]


def test_customer_detail_does_not_look_up_company(page, workflow_ui_client):
    calls = []
    _open_view(page, workflow_ui_client, "customer", company_calls=calls)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    assert page.locator("[data-company-url]").count() == 0
    assert calls == []
