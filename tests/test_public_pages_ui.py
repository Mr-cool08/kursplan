from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from flask import Flask, render_template
from playwright.sync_api import expect
import pytest

import database
from users import user_page


@pytest.fixture
def public_client(project_root, monkeypatch):
    # Render the actual public routes and assets without a database or SMTP.
    app = Flask(
        "public-pages-ui-tests",
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(TESTING=True, SECRET_KEY="public-pages-test-key", DB_PATH="unused")
    app.register_blueprint(user_page)
    monkeypatch.setenv("send_mail", "0")

    def unexpected_database_access(*args, **kwargs):
        pytest.fail("Public page rendering must not read or write customer accounts.")

    for function in ("get_user_identity", "authenticate_user", "check_user_exists", "create_user"):
        monkeypatch.setattr(database, function, unexpected_database_access)
    return app.test_client()


class _PublicPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.inputs = {}
        self.labels = set()
        self.links = []
        self.alerts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.forms.append(attributes)
        elif tag == "input":
            self.inputs[attributes.get("name")] = attributes
        elif tag == "label":
            self.labels.add(attributes.get("for"))
        elif tag == "a":
            self.links.append(attributes.get("href"))
        if attributes.get("role") == "alert":
            self.alerts.append(attributes)


def _parse(response):
    parser = _PublicPageParser()
    parser.feed(response.get_data(as_text=True))
    return parser


def test_home_preserves_both_account_destinations(public_client):
    response = public_client.get("/")
    assert response.status_code == 200
    parser = _parse(response)
    assert "/signup" in parser.links
    assert "/login" in parser.links
    assert parser.forms == []


@pytest.mark.parametrize(
    ("path", "fields"),
    [
        ("/login", {"email": "email", "password": "password"}),
        ("/signup", {
            "name": "text", "email": "email", "password": "password",
            "organization_number": "text",
        }),
    ],
)
def test_account_forms_preserve_post_fields_labels_and_csrf(public_client, path, fields):
    response = public_client.get(path)
    assert response.status_code == 200
    parser = _parse(response)
    assert len(parser.forms) == 1
    form = parser.forms[0]
    assert form["method"].lower() == "post"
    assert form.get("action", path) == path
    for name, field_type in fields.items():
        field = parser.inputs[name]
        assert field["type"] == field_type
        assert "required" in field
        assert field["id"] in parser.labels
    token = parser.inputs["csrf_token"]
    assert token["type"] == "hidden"
    assert token["value"]
    with public_client.session_transaction() as state:
        assert token["value"] in state.values()


def test_login_preserves_booking_destination_after_validation_error(public_client):
    destination = "/bookings/42?proposal_id=7&action=accept"
    response = public_client.get("/login", query_string={"next": destination})
    parser = _parse(response)
    assert parser.inputs["next"]["value"] == destination
    response = public_client.post("/login", data={
        "csrf_token": parser.inputs["csrf_token"]["value"],
        "next": destination,
        "email": "",
        "password": "",
    })
    assert response.status_code == 200
    parser = _parse(response)
    assert parser.inputs["next"]["value"] == destination
    assert len(parser.alerts) == 1
    assert "Ange både e-postadress och lösenord." in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_account_forms_keep_csrf_enforcement(public_client, path):
    response = public_client.post(path, data={"email": "customer@example.test", "password": "test"})
    assert response.status_code == 400


def test_signup_validation_keeps_visible_error_and_does_not_echo_password(public_client):
    parser = _parse(public_client.get("/signup"))
    password = "private-form-value"
    response = public_client.post("/signup", data={
        "csrf_token": parser.inputs["csrf_token"]["value"],
        "password": password,
    })
    assert response.status_code == 400
    assert len(_parse(response).alerts) == 1
    html = response.get_data(as_text=True)
    assert "Fyll i namn, giltig e-postadress, lösenord och organisationsnummer." in html
    assert password not in html


@pytest.mark.parametrize("template", ["login.html", "signup.html"])
def test_auth_error_text_is_escaped(public_client, template):
    with public_client.application.test_request_context():
        html = render_template(
            template, error='<script>alert("unsafe")</script>',
            csrf_token="test-token", next_url="",
        )
    assert '<script>alert("unsafe")</script>' not in html
    assert "&lt;script&gt;" in html


def _open_public_page(page, public_client, path):
    def serve_local(route):
        url = urlsplit(route.request.url)
        response = public_client.get(url.path + (f"?{url.query}" if url.query else ""))
        route.fulfill(
            status=response.status_code,
            body=response.get_data(),
            content_type=response.content_type,
        )

    page.route("http://public.test/**", serve_local)
    page.goto(f"http://public.test{path}")


@pytest.mark.parametrize(("visitor_timezone", "instant", "expected_year"), [
    ("America/Los_Angeles", "2026-12-31T23:30:00+00:00", "2027"),
    ("Asia/Tokyo", "2026-12-31T22:30:00+00:00", "2026"),
])
def test_footer_year_uses_swedish_time_at_new_year(
    new_context, public_client, visitor_timezone, instant, expected_year,
):
    page = new_context(timezone_id=visitor_timezone).new_page()
    page.clock.set_fixed_time(datetime.fromisoformat(instant))
    _open_public_page(page, public_client, "/")
    expect(page.locator("#currentYear")).to_have_text(expected_year)


@pytest.mark.parametrize("path", ["/", "/login", "/signup"])
@pytest.mark.parametrize("width", [320, 768, 1280])
def test_public_pages_fit_mobile_and_desktop_without_footer_overlap(
    page, public_client, path, width
):
    page.set_viewport_size({"width": width, "height": 844})
    _open_public_page(page, public_client, path)
    expect(page.get_by_role("main")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        f"Public page {path} overflows horizontally at {width}px"
    )
    assert page.evaluate(
        "document.querySelector('footer').getBoundingClientRect().top >= "
        "document.querySelector('main').getBoundingClientRect().bottom - 1"
    ), "Footer covers public page content"
    if path != "/":
        for field in page.locator('input:not([type="hidden"]):not([type="checkbox"])').all():
            expect(field).to_be_visible()
            box = field.bounding_box()
            assert box and box["height"] >= 44
        email = page.get_by_label("E-post", exact=False)
        email.focus()
        expect(email).to_be_focused()
        focus = email.evaluate(
            "element => { const s = getComputedStyle(element); "
            "return {outline: s.outlineStyle, width: parseFloat(s.outlineWidth), "
            "shadow: s.boxShadow}; }"
        )
        assert (focus["outline"] != "none" and focus["width"] > 0) or focus["shadow"] != "none"


def test_signup_show_password_preserves_value_and_toggles_back(page, public_client):
    page.set_viewport_size({"width": 320, "height": 844})
    _open_public_page(page, public_client, "/signup")
    password = page.locator("#password")
    password.fill("test-password-value")
    toggle = page.get_by_role("checkbox", name="Visa lösenord")
    toggle.check()
    expect(password).to_have_attribute("type", "text")
    expect(password).to_have_value("test-password-value")
    toggle.uncheck()
    expect(password).to_have_attribute("type", "password")
    expect(password).to_have_value("test-password-value")
