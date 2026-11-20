"""Render the workflow templates with real CSS and local-only browser requests."""

from urllib.parse import urlsplit

from flask import Flask, render_template
from playwright.sync_api import expect
import pytest


STATUS_LABELS = {
    "pending": "Ny förfrågan", "registered": "Registrerad",
    "awaiting_response": "Inväntar kundens svar",
    "new_date_requested": "Nytt datum önskas", "accepted": "Accepterad",
    "rescheduled": "Ombokad", "completed": "Slutförd", "cancelled": "Avbruten",
}
LONG_COURSE = "Anpassad säkerhetsutbildning " + "Utbildning" * 15
LONG_VENUE = "Konferenslokalen, " + "Långadress" * 20


@pytest.fixture
def workflow_ui_client(project_root):
    app = Flask(
        "workflow-ui-tests", template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(TESTING=True, SECRET_KEY="workflow-ui-test-secret")
    paths = {
        "user_page.index": "/", "user_page.dashboard": "/dashboard",
        "user_page.booking": "/booking", "logout": "/logout",
        "user_page.booking_detail": "/bookings/<int:booking_id>",
        "user_page.cancel_booking": "/cancel_booking/<int:booking_id>",
        "user_page.respond_proposal": "/bookings/<int:booking_id>/proposals/<int:proposal_id>/respond",
        "admin_page.dashboard": "/admin/",
        "admin_page.booking_detail": "/admin/bookings/<int:booking_id>",
        "admin_page.preview_proposal": "/admin/bookings/<int:booking_id>/proposal/preview",
        "admin_page.send_proposal": "/admin/bookings/<int:booking_id>/proposal",
        "admin_page.retry_delivery": "/admin/bookings/<int:booking_id>/proposals/<int:proposal_id>/retry",
        "admin_page.change_status": "/admin/bookings/<int:booking_id>/status",
        "admin_page.add_note": "/admin/bookings/<int:booking_id>/notes",
    }
    for endpoint, path in paths.items():
        app.add_url_rule(path, endpoint=endpoint, view_func=lambda **kwargs: "")

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
        "created_at": "2026-11-20 10:00 UTC", "response": None,
        "responded_at": None,
    }
    context = {
        "booking": booking, "bookings": [booking], "name": booking["name"],
        "status_labels": STATUS_LABELS,
        "customer_cancellable": {"pending", "registered", "new_date_requested", "awaiting_response"},
        "csrf_token": "test-csrf-token", "confirmation_token": "signed-preview",
        "admin_targets": ["cancelled"], "active_proposal": proposal,
        "proposals": [proposal], "history": [{
            "created_at": "2026-11-20 10:00 UTC", "actor": "admin",
            "from_status": "pending", "to_status": "awaiting_response",
            "note": "<script>alert('historik')</script>",
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
            "dashboard": "dashboard.html",
        }
        return render_template(templates[view], is_admin=view == "admin", **context)

    return app.test_client()


def _open_view(page, client, view):
    def serve_local(route):
        response = client.get(urlsplit(route.request.url).path)
        route.fulfill(
            status=response.status_code, body=response.get_data(),
            content_type=response.content_type,
        )

    page.route("http://workflow.test/**", serve_local)
    page.goto(f"http://workflow.test/render/{view}")


def test_workflow_detail_and_preview_render_safe_actions(workflow_ui_client):
    customer = workflow_ui_client.get("/render/customer").get_data(as_text=True)
    assert 'method="post"' in customer
    assert '/bookings/42/proposals/3/respond' in customer
    assert 'name="action" value="accept"' in customer
    assert 'name="action" value="new_date"' in customer
    assert 'name="action" value="cancel"' in customer
    assert "&lt;script&gt;" in customer
    assert "<script>alert('historik')</script>" not in customer
    assert "Ny administrativ anteckning" not in customer
    admin = workflow_ui_client.get("/render/admin").get_data(as_text=True)
    assert "/proposals/3/retry" in admin
    assert "E-postmeddelandet har inte skickats" in admin
    assert "Ny administrativ anteckning" in admin
    assert 'name="action" value="accept"' not in admin
    preview = workflow_ui_client.get("/render/preview").get_data(as_text=True)
    assert 'name="confirmation_token" value="signed-preview"' in preview
    assert 'name="confirmed" value="1"' in preview
    assert 'action="/admin/bookings/42/proposal"' in preview


@pytest.mark.parametrize("width", [320, 768, 1280])
@pytest.mark.parametrize("view", ["customer", "admin", "overview", "preview", "dashboard"])
def test_workflow_views_fit_mobile_and_desktop(page, workflow_ui_client, width, view):
    page.set_viewport_size({"width": width, "height": 844})
    _open_view(page, workflow_ui_client, view)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
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
