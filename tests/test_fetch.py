import pytest

from loan_assistant.config import settings
from loan_assistant.scraping import fetch

ROBOTS = "User-agent: *\nDisallow: /hi/\nDisallow: /admin/\n"
PAGE_URL = f"{settings.base_url}/personal-banking/loans/home-loan"


class FakeResponse:
    def __init__(self, status=200, body=b"<html>hello</html>", headers=None):
        self.status_code = status
        self.content = body
        self.text = body.decode()
        self.headers = headers or {}


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout, allow_redirects=True):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(200, ROBOTS.encode())
        return FakeResponse()


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(settings, "request_delay_s", 0.0)


def test_second_run_makes_zero_requests(tmp_path):
    session = FakeSession()
    f1 = fetch.Fetcher(tmp_path, session=session)
    assert not f1.get(PAGE_URL).from_cache
    n_first = len(session.calls)

    session2 = FakeSession()
    f2 = fetch.Fetcher(tmp_path, session=session2)  # new object reads manifest from disk
    res = f2.get(PAGE_URL)
    assert res.from_cache and res.content == b"<html>hello</html>"
    assert session2.calls == [] and n_first >= 2  # robots + page the first time


def test_refresh_refetches(tmp_path):
    fetch.Fetcher(tmp_path, session=FakeSession()).get(PAGE_URL)
    session = FakeSession()
    fetch.Fetcher(tmp_path, session=session, refresh=True).get(PAGE_URL)
    assert PAGE_URL in session.calls


def test_manifest_has_required_columns(tmp_path):
    fetch.Fetcher(tmp_path, session=FakeSession()).get(PAGE_URL)
    header = (tmp_path / "manifest.csv").read_text().splitlines()[0].split(",")
    assert {"url", "http_status", "fetched_at", "bytes", "sha256"} <= set(header)


def test_robots_disallowed_url_is_not_requested(tmp_path):
    session = FakeSession()
    res = fetch.Fetcher(tmp_path, session=session).get(f"{settings.base_url}/hi/home-loan")
    assert res.status == 403
    assert not any(c.endswith("/hi/home-loan") for c in session.calls)


def test_failed_fetch_is_retried_next_run(tmp_path):
    class Down(FakeSession):
        def get(self, url, timeout, allow_redirects=True):
            self.calls.append(url)
            return (
                FakeResponse(200, ROBOTS.encode())
                if url.endswith("robots.txt")
                else FakeResponse(503, b"")
            )

    fetch.Fetcher(tmp_path, session=Down()).get(PAGE_URL)
    session = FakeSession()
    res = fetch.Fetcher(tmp_path, session=session).get(PAGE_URL)
    assert res.status == 200 and PAGE_URL in session.calls


def test_redirect_off_official_host_is_not_followed_and_is_cached(tmp_path):
    msme = f"{settings.base_url}/msme"
    old = "https://www.bankofmaharashtra.in/msme_large_credit"

    class Redirects(FakeSession):
        def get(self, url, timeout, allow_redirects=True):
            self.calls.append(url)
            if url.endswith("robots.txt"):
                return FakeResponse(200, ROBOTS.encode())
            return FakeResponse(301, b"", {"Location": old})

    session = Redirects()
    res = fetch.Fetcher(tmp_path, session=session).get(msme)
    assert res.status == 301 and old not in session.calls
    session2 = Redirects()
    assert fetch.Fetcher(tmp_path, session=session2).get(msme).from_cache
    assert session2.calls == []


def test_redirect_on_official_host_is_followed(tmp_path):
    a, b = f"{settings.base_url}/a", f"{settings.base_url}/b"

    class Hop(FakeSession):
        def get(self, url, timeout, allow_redirects=True):
            self.calls.append(url)
            if url.endswith("robots.txt"):
                return FakeResponse(200, ROBOTS.encode())
            if url == a:
                return FakeResponse(301, b"", {"Location": "/b"})
            return FakeResponse(200, b"final")

    session = Hop()
    assert fetch.Fetcher(tmp_path, session=session).get(a).content == b"final"
    assert b in session.calls
