import re

from playwright.sync_api import expect


def _load_form(page, html, project_root):
    # The Flask client renders the real Jinja template. The production script is
    # then injected from disk because set_content() has no HTTP origin from which
    # it could load /static/js/book.js.
    html_without_script = re.sub(
        r'<script[^>]+src="[^"]*book\.js[^"]*"[^>]*></script>',
        "",
        html,
        flags=re.IGNORECASE,
    )
    page.set_content(html_without_script)
    page.add_style_tag(path=str(project_root / "static" / "style" / "book.css"))
    page.add_script_tag(path=str(project_root / "static" / "js" / "book.js"))


def _field_state(page, selector):
    return page.locator(selector).evaluate(
        "element => ({disabled: element.disabled, required: element.required, value: element.value})"
    )


def test_booking_form_dynamic_modes_search_keyboard_and_mobile(
    page, logged_in_client, project_root
):
    response = logged_in_client.get("/booking")
    assert response.status_code == 200
    page.set_viewport_size({"width": 390, "height": 844})
    _load_form(page, response.get_data(as_text=True), project_root)

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
    search.fill("sÄk")
    option_texts = course_select.locator("option").all_text_contents()
    assert "Säkra lyft" in option_texts
    assert "HLR" not in option_texts

    search.fill("UTBILDNING")
    option_texts = course_select.locator("option").all_text_contents()
    assert "Liftutbildning" in option_texts
    assert "Brandskyddsutbildning" in option_texts

    search.fill("finns inte")
    expect(page.locator("#utbildning-sok-status")).to_have_text(
        "Inga utbildningar matchar din sökning."
    )

    search.fill("hlr")
    course_select.focus()
    course_select.press("ArrowDown")
    course_select.press("Enter")
    expect(course_select).to_have_value("HLR")

    page.locator('input[name="utbildningskalla"][value="fritext"]').check()
    expect(page.locator("#catalog-course-fields")).to_be_hidden()
    expect(page.locator("#custom-course-fields")).to_be_visible()
    assert _field_state(page, "#utbildning-sok")["disabled"] is True
    assert _field_state(page, "#utbildning")["disabled"] is True
    custom_course = page.locator("#egen-utbildning")
    assert _field_state(page, "#egen-utbildning")["required"] is True
    custom_course.fill("Egen kurs")
    page.locator('input[name="utbildningskalla"][value="katalog"]').check()
    assert _field_state(page, "#egen-utbildning") == {
        "disabled": True,
        "required": False,
        "value": "",
    }

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

    # Native controls remain reachable with the keyboard and retain a visible
    # browser/CSS focus indicator at a narrow mobile viewport.
    search.focus()
    focus_style = search.evaluate(
        "element => { const style = getComputedStyle(element); "
        "return {outline: style.outlineStyle, shadow: style.boxShadow}; }"
    )
    assert focus_style["outline"] != "none" or focus_style["shadow"] != "none"


def test_rerendered_error_initializes_the_preserved_modes(
    page, logged_in_client, valid_booking_data, project_root
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

    _load_form(page, response.get_data(as_text=True), project_root)

    expect(page.locator("#date-range-fields")).to_be_visible()
    expect(page.locator("#exact-date-fields")).to_be_hidden()
    expect(page.locator("#custom-course-fields")).to_be_visible()
    expect(page.locator("#catalog-course-fields")).to_be_hidden()
    expect(page.locator("#local-address-fields")).to_be_visible()
    expect(page.locator("#onskat-startdatum")).to_have_value("2027-10-01")
    expect(page.locator("#onskat-slutdatum")).to_have_value("2027-10-03")
    expect(page.locator("#egen-utbildning")).to_have_value("Bevarad specialkurs")
    expect(page.locator("#lokaladress")).to_have_value("Bevarad adress 7")
