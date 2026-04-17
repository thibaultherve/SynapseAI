"""Per-page PDF extraction timeout budget."""

import time

import pytest

from app.utils import text_extraction


class _FakePage:
    """Returns ``text`` after sleeping ``delay`` seconds synchronously."""

    def __init__(self, text: str, delay: float):
        self._text = text
        self._delay = delay

    def extract_text(self) -> str:
        if self._delay:
            time.sleep(self._delay)
        return self._text


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_per_page_timeout_stops_iteration_after_slow_page(monkeypatch):
    """A page that exceeds PDF_PAGE_TIMEOUT must break the iteration."""
    monkeypatch.setattr(text_extraction, "PDF_PAGE_TIMEOUT", 0.05)

    pages = [
        _FakePage("fast page content", delay=0),
        _FakePage("SLOW PAGE CONTENT", delay=0.2),  # exceeds budget -> break
        _FakePage("should-not-appear", delay=0),
    ]

    def fake_open(file_path):
        return _FakePdf(pages)

    class FakePlumber:
        @staticmethod
        def open(path):
            return fake_open(path)

    monkeypatch.setitem(__import__("sys").modules, "pdfplumber", FakePlumber)

    result = text_extraction._extract_pdf_sync("ignored.pdf")

    assert "fast page content" in result
    assert "SLOW PAGE CONTENT" in result  # the slow page itself is kept
    assert "should-not-appear" not in result


def test_all_fast_pages_fully_extracted(monkeypatch):
    """Baseline: when no page exceeds the budget, all pages are extracted."""
    monkeypatch.setattr(text_extraction, "PDF_PAGE_TIMEOUT", 1.0)

    pages = [_FakePage(f"page {i}", delay=0) for i in range(3)]

    class FakePlumber:
        @staticmethod
        def open(path):
            return _FakePdf(pages)

    monkeypatch.setitem(__import__("sys").modules, "pdfplumber", FakePlumber)

    result = text_extraction._extract_pdf_sync("ignored.pdf")

    for i in range(3):
        assert f"page {i}" in result
