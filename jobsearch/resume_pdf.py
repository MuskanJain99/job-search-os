"""Render resume JSON to a single-column, ATS-friendly A4 PDF (HTML → Chromium print)."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

_env = Environment(loader=PackageLoader("jobsearch", "templates"), autoescape=select_autoescape(["html"]))


def render_html(content: dict, header: dict) -> str:
    return _env.get_template("resume.html").render(c=content, h=header)


def safe_name(*parts: str) -> str:
    return "_".join(re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") for p in parts if p)[:120] + ".pdf"


def _render(html: str, out_path: Path) -> None:
    import os

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = os.getenv("CHROMIUM_EXECUTABLE") or None
        try:
            browser = p.chromium.launch(executable_path=exe)
        except Exception:  # bundled Chromium not installed (e.g. on the laptop): use installed Google Chrome
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.pdf(path=str(out_path), format="A4", print_background=True, prefer_css_page_size=True)
        # Keep to one page: if it overflows, shrink text slightly and retry once.
        pages = page.evaluate("() => Math.ceil(document.body.scrollHeight / 1050)")
        if pages > 1:
            page.add_style_tag(content="body{font-size:8.9pt;line-height:1.25} ul{margin-bottom:2pt}")
            page.pdf(path=str(out_path), format="A4", print_background=True, prefer_css_page_size=True)
        browser.close()


def render_pdf(content: dict, header: dict, out_path: Path) -> Path:
    """Render in a worker thread: callers (the laptop helper) may already be inside a Playwright
    sync session, and Playwright's sync API refuses to nest within the same thread."""
    from concurrent.futures import ThreadPoolExecutor

    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(content, header)
    with ThreadPoolExecutor(max_workers=1) as ex:
        ex.submit(_render, html, out_path).result()
    return out_path
