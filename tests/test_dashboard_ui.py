from html.parser import HTMLParser
from math import isfinite
from urllib.parse import urlsplit

from flask import Flask, redirect, session, url_for
from playwright.sync_api import expect
import pytest

import database
from users import user_page


COURSE = "Fördjupad säkerhetsutbildning & riskbedömning " + "Utbildning" * 12
ADDRESS = "Konferensanläggningen, " + "Långadress" * 16 + " 123, Göteborg"
DATE_RANGE = "2027-03-18 – 2027-03-22"
CUSTOMER_EMAIL = "dashboard-customer@example.com"


@pytest.fixture
def dashboard_client(project_root, monkeypatch):
    # Exercise the real route and templates without a database server or mail.
    app = Flask(
        "dashboard-ui-tests",
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(TESTING=True, SECRET_KEY="dashboard-ui-test-key", DB_PATH="unused")
    app.register_blueprint(user_page)

    @app.route("/logout", endpoint="logout")
    def logout():
        session.clear()
        return redirect(url_for("user_page.index"))

    def booking(booking_id, course, participants, city, venue, day, status):
        return dict(id=booking_id, name="Testkund", email=CUSTOMER_EMAIL,
                    organization_number="5566778899", utbildning=course, antal=participants,
                    ort=city, lokal=venue, datum=day, status=status,
                    datumtyp="intervall" if " – " in day else "exakt",
                    onskat_datum=day if " – " not in day else None,
                    onskat_startdatum="2027-03-18" if " – " in day else None,
                    onskat_slutdatum="2027-03-22" if " – " in day else None,
                    onskad_ort=city, lokaladress=venue,
                    faststallt_datum=day if status == "accepted" else None,
                    faststalld_ort=city if status == "accepted" else None,
                    faststalld_lokal=venue if status == "accepted" else None)

    bookings = [
        booking(11, COURSE, 12, None, ADDRESS, DATE_RANGE, "pending"),
        booking(12, "Bekräftad HLR", 5, "Uppsala", "Säkerhetsutbildarna ordnar lokalen", "2027-04-15", "accepted"),
        booking(13, "Avbokad kurs", 3, "Göteborg", "Testgatan 1", "2027-05-01", "cancelled"),
    ]

    def get_bookings(db_path, email=None, status=None):
        assert email == CUSTOMER_EMAIL
        assert db_path == "unused"
        return bookings

    monkeypatch.setattr(database, "list_booking_details", get_bookings)
    monkeypatch.setattr(database, "get_user_identity", lambda *args: {
        "id": 1, "name": "Anna " + "Långtefternamn" * 12, "email": CUSTOMER_EMAIL,
        "organization_number": "5566778899", "role": "user",
    })
    monkeypatch.setenv("send_mail", "0")
    client = app.test_client()
    with client.session_transaction() as state:
        state.update(
            user_id=1,
            user_name="Anna " + "Långtefternamn" * 12,
            user_email=CUSTOMER_EMAIL,
            organization_number="5566778899",
            role="user",
        )
    return client, bookings


class _DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.articles = []
        self.forms = []
        self.csrf_tokens = []
        self.headings = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "article":
            self.articles.append(attributes)
        elif tag == "h3":
            self.headings.add(attributes.get("id"))
        elif tag == "form":
            self.forms.append(attributes)
        elif tag == "input" and attributes.get("name") == "csrf_token":
            self.csrf_tokens.append(attributes.get("value"))


def _open_dashboard(page, dashboard_client):
    client, _ = dashboard_client

    def serve_local(route):
        url = urlsplit(route.request.url)
        response = client.get(url.path)
        route.fulfill(
            status=response.status_code,
            body=response.get_data(),
            content_type=response.content_type,
        )

    # Serve Flask's actual HTML and static assets locally; no external requests.
    page.route("http://dashboard.test/**", serve_local)
    page.goto("http://dashboard.test/dashboard")


def test_dashboard_cards_preserve_fields_status_and_csrf(dashboard_client):
    client, _ = dashboard_client
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parser = _DashboardParser()
    parser.feed(html)

    assert len(parser.articles) == 3
    for article in parser.articles:
        assert "booking-card" in article.get("class", "").split()
        assert article.get("aria-labelledby") in parser.headings
    assert len(parser.forms) == 1
    assert parser.forms[0]["method"].lower() == "post"
    assert parser.forms[0]["action"] == "/cancel_booking/11"
    assert len(parser.csrf_tokens) >= 1 and parser.csrf_tokens[0]
    with client.session_transaction() as state:
        assert parser.csrf_tokens[0] in state.values()
    for text in (DATE_RANGE, ADDRESS, "Uppsala", "2027-04-15", "Inte angiven",
                 "Ny förfrågan", "Accepterad", "Avbruten", "Säkerhetsutbildarna ordnar lokalen"):
        assert text in html
    assert "säkerhetsutbildning &amp; riskbedömning" in html
    assert '<table' not in html


@pytest.mark.parametrize("width", [320, 390, 768, 1280])
def test_dashboard_cards_fit_mobile_and_desktop(page, dashboard_client, width):
    page.set_viewport_size({"width": width, "height": 844})
    _open_dashboard(page, dashboard_client)
    cards = page.get_by_role("article")
    expect(cards).to_have_count(3)
    pending = cards.filter(has_text=COURSE)
    expect(pending.get_by_role("heading", level=3)).to_have_text(COURSE)
    expect(pending.get_by_text(DATE_RANGE, exact=True)).to_be_visible()
    expect(pending.get_by_text(ADDRESS, exact=True)).to_be_visible()
    expect(pending.get_by_text("Inte angiven", exact=True)).to_be_visible()
    expect(pending.locator(".booking-details dt")).to_have_text(
        ["Önskat datum", "Antal deltagare", "Ort", "Lokal"]
    )
    expect(pending.locator(".booking-details dd")).to_have_text(
        [DATE_RANGE, "12", "Inte angiven", ADDRESS]
    )
    expect(cards.filter(has_text="Bekräftad HLR").get_by_role("button")).to_have_count(0)
    expect(cards.filter(has_text="Avbokad kurs").get_by_role("button")).to_have_count(0)

    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), f"Dashboard overflows horizontally at {width}px"
    for card in cards.all():
        assert card.evaluate("element => element.scrollWidth <= element.clientWidth")

    first = cards.nth(0).bounding_box()
    second = cards.nth(1).bounding_box()
    assert first and second
    if width <= 390:
        assert second["y"] >= first["y"] + first["height"]
    elif width == 1280:
        assert abs(first["y"] - second["y"]) <= 1

    controls = [page.get_by_role("link", name="Gör ny bokning"),
                pending.get_by_role("button", name="Avbryt"),
                page.get_by_role("link", name="Logga ut")]
    for control in controls:
        box = control.bounding_box()
        assert box and box["height"] >= 44 and box["width"] >= 44
        control.focus()
        expect(control).to_be_focused()
        focus = control.evaluate(
            "element => { const s = getComputedStyle(element); "
            "return {outline: s.outlineStyle, width: parseFloat(s.outlineWidth), "
            "shadow: s.boxShadow}; }"
        )
        assert (focus["outline"] != "none" and focus["width"] > 0) or focus["shadow"] != "none"

    controls[0].focus()
    page.keyboard.press("Tab")
    expect(pending.get_by_role("link", name="Visa bokning")).to_be_focused()
    page.keyboard.press("Tab")
    expect(controls[1]).to_be_focused()
    assert page.evaluate(
        "document.querySelector('footer').getBoundingClientRect().top >= "
        "document.querySelector('main').getBoundingClientRect().bottom - 1"
    ), "Footer covers dashboard content"


def test_empty_dashboard_offers_booking_on_mobile(page, dashboard_client):
    _, bookings = dashboard_client
    bookings.clear()
    page.set_viewport_size({"width": 320, "height": 640})
    _open_dashboard(page, dashboard_client)

    expect(page.get_by_role("article")).to_have_count(0)
    expect(page.get_by_text("Du har inga bokningar ännu.", exact=True)).to_be_visible()
    booking_links = page.get_by_role("link", name="Gör ny bokning")
    assert booking_links.count() >= 1
    for link in booking_links.all():
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", "/booking")
    empty_state_link = page.get_by_role("link", name="Hitta din utbildning")
    expect(empty_state_link).to_be_visible()
    expect(empty_state_link).to_have_attribute("href", "/booking")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def _text_contrasts(elements):
    return elements.evaluate_all(r"""elements => {
        const rgba = color => {
            const values = color.match(/[\d.]+/g).map(Number);
            return [...values.slice(0, 3), values[3] ?? 1];
        };
        const composite = (front, back) => [
            ...front.slice(0, 3).map((value, i) =>
                value * front[3] + back[i] * (1 - front[3])),
            1
        ];
        const background = element => {
            if (!element) return [255, 255, 255, 1];
            const color = rgba(getComputedStyle(element).backgroundColor);
            return color[3] === 1 ? color : composite(color, background(element.parentElement));
        };
        const luminance = color => color.slice(0, 3).map(value => {
            const s = value / 255;
            return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
        return elements.map(element => {
            const bg = background(element);
            const fg = composite(rgba(getComputedStyle(element).color), bg);
            const light = luminance(fg);
            const dark = luminance(bg);
            return {
                text: element.textContent.trim().slice(0, 100),
                ratio: (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05)
            };
        });
    }""")


def test_dashboard_text_actions_and_all_statuses_have_readable_contrast(
    page, dashboard_client
):
    _, bookings = dashboard_client
    covered = {booking["status"] for booking in bookings}
    for index, status in enumerate(database.BOOKING_STATUSES, start=100):
        if status not in covered:
            bookings.append(bookings[0] | {"id": index, "status": status})

    page.set_viewport_size({"width": 390, "height": 844})
    _open_dashboard(page, dashboard_client)
    text = page.locator(
        "main h1, main h2, main h3, main p, main dt, main dd, "
        "main .booking-reference, main .booking-count, main .status-pill, "
        "main .workflow-message, main a, main button"
    )
    failures = [
        result for result in _text_contrasts(text)
        if not isfinite(result["ratio"]) or result["ratio"] < 4.5
    ]
    assert not failures, f"Dashboard text falls below 4.5:1 contrast: {failures}"

    for action in (
        page.get_by_role("link", name="Gör ny bokning"),
        page.get_by_role("link", name="Visa bokning").first,
        page.get_by_role("button", name="Avbryt").first,
        page.get_by_role("link", name="Logga ut"),
    ):
        action.hover()
        action.focus()
        result = _text_contrasts(action)[0]
        assert result["ratio"] >= 4.5, f"Hovered/focused action has low contrast: {result}"
