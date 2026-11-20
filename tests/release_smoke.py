"""Check the web image from another container on its test network."""

import json
import os
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, build_opener


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.home_heading = False
        self.links = set()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "h1" and attributes.get("id") == "home-heading":
            self.home_heading = True
        if tag == "a":
            self.links.add(attributes.get("href"))
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


def smoke_test(base_url="http://kursplan:8000"):
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    with client.open(f"{base_url}/health", timeout=10) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "ok"}

    with client.open(f"{base_url}/", timeout=10) as response:
        assert response.status == 200, "Startsidan svarar inte med HTTP 200."
        assert urlsplit(response.url).path == "/", "Startsidan omdirigerar till en annan sida."
        assert response.headers.get_content_type() == "text/html", "Startsidan returnerar inte HTML."
        parser = PageParser()
        parser.feed(response.read().decode("utf-8"))
        assert parser.home_heading, "Startsidans huvudrubrik saknas."
        assert "/signup" in parser.links, "Startsidan saknar länken för att skapa konto."
        assert "/login" in parser.links, "Startsidan saknar inloggningslänken."

    with client.open(f"{base_url}/static/js/book.js", timeout=10) as response:
        assert response.headers.get_content_type() in {
            "application/javascript",
            "text/javascript",
        }
        assert response.read().strip(), "Bokningsskriptet är tomt."

    with client.open(f"{base_url}/admin/", timeout=10) as response:
        assert urlsplit(response.url).path == "/login"

    with client.open(f"{base_url}/login", timeout=10) as response:
        parser = PageParser()
        parser.feed(response.read().decode("utf-8"))
        assert parser.token, "Inloggningsformuläret saknar CSRF-skydd."
    credentials = urlencode({
        "email": os.environ["test_email"], "password": os.environ["test_password"],
        "csrf_token": parser.token,
    }).encode("utf-8")
    with client.open(f"{base_url}/login", data=credentials, timeout=10) as response:
        assert urlsplit(response.url).path == "/admin/", "Inloggningen misslyckades."
        assert "Bokningar" in response.read().decode("utf-8")

    with client.open(f"{base_url}/booking", timeout=10) as response:
        assert 'id="booking-form"' in response.read().decode("utf-8")


if __name__ == "__main__":
    smoke_test()
