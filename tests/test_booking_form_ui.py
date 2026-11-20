from urllib.parse import urlsplit

from playwright.sync_api import expect
import pytest


def _load_form(page, html, client):
    def serve_local(route):
        url = urlsplit(route.request.url)
        if url.path == "/booking":
            route.fulfill(status=200, body=html, content_type="text/html; charset=utf-8")
            return
        response = client.get(url.path)
        route.fulfill(
            status=response.status_code,
            body=response.get_data(),
            content_type=response.content_type,
        )

    # Load the real template's full stylesheet cascade and production script.
    # No local HTTP server or external requests are needed.
    page.route("http://booking.test/**", serve_local)
    page.goto("http://booking.test/booking")


def _field_state(page, selector):
    return page.locator(selector).evaluate(
        "element => ({disabled: element.disabled, required: element.required, value: element.value})"
    )


def test_booking_form_dynamic_modes_search_keyboard_and_mobile(
    page, logged_in_client
):
    response = logged_in_client.get("/booking")
    assert response.status_code == 200
    page.set_viewport_size({"width": 390, "height": 844})
    _load_form(page, response.get_data(as_text=True), logged_in_client)

    expect(page.locator("#booking-form")).to_be_visible()
    expect(page.locator("#exact-date-fields")).to_be_visible()
    expect(page.locator("#date-range-fields")).to_be_hidden()
    assert _field_state(page, "#onskat-datum") == {
        "disabled": False,
        "required": True,
        "value": "",
    }
    assert _field_state(page, "#onskat-startdatum")["disabled"] is True
    assert _field_state(page, "#onskat-slutdatum")["disabled"] is True

    page.locator("#onskat-datum").fill("2027-02-01")
    page.locator('input[name="datumtyp"][value="intervall"]').check()
    expect(page.locator("#exact-date-fields")).to_be_hidden()
    expect(page.locator("#date-range-fields")).to_be_visible()
    assert _field_state(page, "#onskat-datum") == {
        "disabled": True,
        "required": False,
        "value": "",
    }
    for selector in ("#onskat-startdatum", "#onskat-slutdatum"):
        state = _field_state(page, selector)
        assert state["disabled"] is False
        assert state["required"] is True

    search = page.locator("#utbildning-sok")
    course_select = page.locator("#utbildning")
    expect(search).to_have_attribute("role", "combobox")
    expect(course_select).to_be_hidden()
    expect(page.locator("#catalog-course-fields input:visible, #catalog-course-fields select:visible")).to_have_count(1)
    search.fill("sÄk")
    option_texts = page.get_by_role("option").all_text_contents()
    assert "Säkra lyft" in option_texts
    assert "HLR" not in option_texts

    search.fill("UTBILDNING")
    option_texts = page.get_by_role("option").all_text_contents()
    assert "Liftutbildning" in option_texts
    assert "Brandskyddsutbildning" in option_texts

    search.fill("finns inte")
    expect(page.locator("#utbildning-sok-status")).to_have_text(
        "Inga utbildningar matchar din sökning."
    )

    search.fill("hlr")
    search.press("ArrowDown")
    search.press("Enter")
    expect(search).to_have_value("HLR")
    expect(course_select).to_have_value("HLR")
    expect(search).to_have_attribute("aria-expanded", "false")

    page.locator('input[name="utbildningskalla"][value="fritext"]').check()
    expect(page.locator("#catalog-course-fields")).to_be_hidden()
    expect(page.locator("#custom-course-fields")).to_be_visible()
    assert _field_state(page, "#utbildning-sok")["disabled"] is True
    assert _field_state(page, "#utbildning")["disabled"] is True
    assert _field_state(page, "#utbildning")["value"] == ""
    expect(page.locator("#utbildning-lista")).to_be_hidden()
    custom_course = page.locator("#egen-utbildning")
    assert _field_state(page, "#egen-utbildning")["required"] is True
    custom_course.fill("Egen kurs")
    page.locator('input[name="utbildningskalla"][value="katalog"]').check()
    assert _field_state(page, "#egen-utbildning") == {
        "disabled": True,
        "required": False,
        "value": "",
    }
    search.fill("Ogiltig katalogkurs")
    assert search.evaluate("element => element.checkValidity()") is False
    page.locator('input[name="utbildningskalla"][value="fritext"]').check()
    page.locator('input[name="utbildningskalla"][value="katalog"]').check()
    search.fill("HLR")
    search.press("ArrowDown")
    search.press("Enter")
    expect(search).to_have_value("HLR")
    expect(course_select).to_have_value("HLR")
    assert search.evaluate("element => element.checkValidity()") is True

    page.locator('input[name="lokalansvar"][value="kund"]').check()
    expect(page.locator("#local-address-fields")).to_be_visible()
    address = page.locator("#lokaladress")
    assert _field_state(page, "#lokaladress")["required"] is True
    address.fill("Testgatan 1")
    page.locator(
        'input[name="lokalansvar"][value="sakerhetsutbildarna"]'
    ).check()
    expect(page.locator("#local-address-fields")).to_be_hidden()
    assert _field_state(page, "#lokaladress") == {
        "disabled": True,
        "required": False,
        "value": "",
    }

    # The editable picker remains reachable with the keyboard and retains a
    # visible focus indicator at a narrow mobile viewport.
    search.focus()
    focus_style = search.evaluate(
        "element => { const style = getComputedStyle(element); "
        "return {outline: style.outlineStyle, shadow: style.boxShadow}; }"
    )
    assert focus_style["outline"] != "none" or focus_style["shadow"] != "none"


def test_course_picker_click_keyboard_dismissal_validation_and_submission(
    page, logged_in_client
):
    response = logged_in_client.get("/booking")
    _load_form(page, response.get_data(as_text=True), logged_in_client)
    search = page.locator("#utbildning-sok")
    choices = page.locator("#utbildning-lista")
    course_select = page.locator("#utbildning")

    expect(search).to_have_attribute("aria-controls", "utbildning-lista")
    expect(search).to_have_attribute("aria-autocomplete", "list")
    assert _field_state(page, "#utbildning-sok")["required"] is True
    assert search.evaluate("element => element.checkValidity()") is False

    search.click()
    expect(choices).to_be_visible()
    expect(search).to_have_attribute("aria-expanded", "true")
    page.get_by_role("option", name="Säkra lyft", exact=True).click()
    expect(search).to_have_value("Säkra lyft")
    expect(course_select).to_have_value("Säkra lyft")
    expect(choices).to_be_hidden()
    assert search.evaluate("element => element.checkValidity()") is True
    assert page.locator("#booking-form").evaluate(
        "form => new FormData(form).getAll('utbildning')"
    ) == ["Säkra lyft"]

    search.fill("Det finns ingen sådan kurs")
    expect(course_select).to_have_value("")
    expect(page.locator("#utbildning-sok-status")).to_have_text(
        "Inga utbildningar matchar din sökning."
    )
    assert search.evaluate("element => element.checkValidity()") is False
    search.press("Enter")
    assert page.url == "http://booking.test/booking"
    expect(course_select).to_have_value("")

    search.fill("")
    search.press("ArrowDown")
    active_id = search.get_attribute("aria-activedescendant")
    assert active_id
    expect(page.locator(f"[id='{active_id}']")).to_be_visible()
    search.press("ArrowDown")
    second_id = search.get_attribute("aria-activedescendant")
    assert second_id and second_id != active_id
    search.press("ArrowUp")
    expect(search).to_have_attribute("aria-activedescendant", active_id)
    search.press("Escape")
    expect(choices).to_be_hidden()
    expect(search).to_be_focused()
    assert not search.get_attribute("aria-activedescendant")
    assert page.url == "http://booking.test/booking"

    search.click()
    expect(choices).to_be_visible()
    page.get_by_role("heading", level=1).click()
    expect(choices).to_be_hidden()
    search.click()
    expect(choices).to_be_visible()
    search.press("Tab")
    expect(choices).to_be_hidden()
    expect(search).not_to_be_focused()
    assert not search.get_attribute("aria-activedescendant")


def test_course_picker_preserves_catalog_selection_after_server_error(
    page, logged_in_client, valid_booking_data
):
    response = logged_in_client.post("/booking", data=valid_booking_data | {"antal": "0"})
    assert response.status_code == 400
    _load_form(page, response.get_data(as_text=True), logged_in_client)

    search = page.locator("#utbildning-sok")
    expect(search).to_have_value("HLR")
    expect(page.locator("#utbildning")).to_have_value("HLR")
    expect(search).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#utbildning-lista")).to_be_hidden()
    assert search.evaluate("element => element.checkValidity()") is True


def test_course_picker_keeps_native_selection_without_javascript(
    browser, logged_in_client
):
    response = logged_in_client.get("/booking")
    with browser.new_context(java_script_enabled=False) as context:
        page = context.new_page()
        _load_form(page, response.get_data(as_text=True), logged_in_client)
        course_select = page.locator("#utbildning")
        expect(course_select).to_be_visible()
        expect(page.locator("#utbildning-sok")).to_be_hidden()
        course_select.select_option("HLR")
        assert page.locator("#booking-form").evaluate(
            "form => new FormData(form).getAll('utbildning')"
        ) == ["HLR"]


def test_course_picker_allows_touch_selection_on_mobile(browser, logged_in_client):
    response = logged_in_client.get("/booking")
    with browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    ) as context:
        page = context.new_page()
        _load_form(page, response.get_data(as_text=True), logged_in_client)
        search = page.locator("#utbildning-sok")
        search.tap()
        expect(page.locator("#utbildning-lista")).to_be_visible()
        page.get_by_role("option", name="HLR", exact=True).tap()
        expect(search).to_have_value("HLR")
        expect(page.locator("#utbildning")).to_have_value("HLR")
        expect(page.locator("#utbildning-lista")).to_be_hidden()
        assert page.locator("#booking-form").evaluate(
            "form => new FormData(form).getAll('utbildning')"
        ) == ["HLR"]


def test_rerendered_error_initializes_the_preserved_modes(
    page, logged_in_client, valid_booking_data
):
    data = valid_booking_data | {
        "datumtyp": "intervall",
        "onskat_datum": "",
        "onskat_startdatum": "2027-10-01",
        "onskat_slutdatum": "2027-10-03",
        "utbildningskalla": "fritext",
        "utbildning": "",
        "egen_utbildning": "Bevarad specialkurs",
        "antal": "0",
        "lokalansvar": "kund",
        "lokaladress": "Bevarad adress 7",
    }
    response = logged_in_client.post("/booking", data=data)
    assert response.status_code == 400

    _load_form(page, response.get_data(as_text=True), logged_in_client)

    expect(page.locator("#date-range-fields")).to_be_visible()
    expect(page.locator("#exact-date-fields")).to_be_hidden()
    expect(page.locator("#custom-course-fields")).to_be_visible()
    expect(page.locator("#catalog-course-fields")).to_be_hidden()
    expect(page.locator("#local-address-fields")).to_be_visible()
    expect(page.locator("#onskat-startdatum")).to_have_value("2027-10-01")
    expect(page.locator("#onskat-slutdatum")).to_have_value("2027-10-03")
    expect(page.locator("#egen-utbildning")).to_have_value("Bevarad specialkurs")
    expect(page.locator("#lokaladress")).to_have_value("Bevarad adress 7")


@pytest.mark.parametrize("width", [320, 390, 768, 1280])
def test_booking_form_fits_viewport_with_all_conditional_fields(
    page, logged_in_client, width
):
    response = logged_in_client.get("/booking")
    assert response.status_code == 200
    page.set_viewport_size({"width": width, "height": 844})
    _load_form(page, response.get_data(as_text=True), logged_in_client)

    back = page.get_by_role("link", name="Till mina bokningar")
    expect(back).to_be_visible()
    expect(back).to_have_attribute("href", "/dashboard")
    search = page.locator("#utbildning-sok")
    search.click()
    choices = page.locator("#utbildning-lista")
    expect(choices).to_be_visible()
    box = choices.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
    first_option = page.get_by_role("option").first
    option_box = first_option.bounding_box()
    assert option_box and option_box["height"] >= 44
    first_option.click()
    expect(choices).to_be_hidden()
    page.locator('input[name="datumtyp"][value="intervall"]').check()
    page.locator('input[name="utbildningskalla"][value="fritext"]').check()
    page.locator('input[name="lokalansvar"][value="kund"]').check()

    for selector, value in (
        ("#onskat-startdatum", "2027-10-01"),
        ("#onskat-slutdatum", "2027-10-03"),
        ("#egen-utbildning", "Egen utbildning med ett långt beskrivande namn"),
        ("#lokaladress", "Konferensanläggningen, Utbildningsvägen 123, Göteborg"),
        ("#antal", "12"),
    ):
        field = page.locator(selector)
        expect(field).to_be_visible()
        field.fill(value)
        expect(field).to_have_value(value)
        box = field.bounding_box()
        assert box and box["height"] >= 44
        assert box["x"] >= 0 and box["x"] + box["width"] <= width

    expect(page.get_by_role("button", name="Skicka förfrågan")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        f"Booking form overflows horizontally at {width}px"
    )
    assert page.evaluate(
        "document.querySelector('footer').getBoundingClientRect().top >= "
        "document.querySelector('main').getBoundingClientRect().bottom - 1"
    ), "Footer covers booking form content"
