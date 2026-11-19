"""Check the web image from another container on its test network."""

import json
import os
from http.cookiejar import CookieJar
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, build_opener


def smoke_test(base_url="http://kursplan:8000"):
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    with client.open(f"{base_url}/health", timeout=10) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "ok"}

    with client.open(f"{base_url}/", timeout=10) as response:
        assert "Välkommen" in response.read().decode("utf-8")

    with client.open(f"{base_url}/static/js/book.js", timeout=10) as response:
        assert response.headers.get_content_type() in {
            "application/javascript",
            "text/javascript",
        }
        assert response.read().strip(), "Bokningsskriptet är tomt."

    with client.open(f"{base_url}/admin/", timeout=10) as response:
        assert urlsplit(response.url).path == "/login"

    credentials = urlencode(
        {"email": os.environ["test_email"], "password": os.environ["test_password"]}
    ).encode("utf-8")
    with client.open(f"{base_url}/login", data=credentials, timeout=10) as response:
        assert urlsplit(response.url).path == "/admin/", "Inloggningen misslyckades."
        assert "Bokningar" in response.read().decode("utf-8")

    with client.open(f"{base_url}/booking", timeout=10) as response:
        assert 'id="booking-form"' in response.read().decode("utf-8")


if __name__ == "__main__":
    smoke_test()
