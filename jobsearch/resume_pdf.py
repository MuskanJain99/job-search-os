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


def render_pdf(content: dict, header: dict, out_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(content, header)
    import os

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None)
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.pdf(path=str(out_path), format="A4", print_background=True, prefer_css_page_size=True)
        # Keep to one page: if it overflows, shrink text slightly and retry once.
        pages = page.evaluate("() => Math.ceil(document.body.scrollHeight / 1050)")
        if pages > 1:
            page.add_style_tag(content="body{font-size:8.9pt;line-height:1.25} ul{margin-bottom:2pt}")
            page.pdf(path=str(out_path), format="A4", print_background=True, prefer_css_page_size=True)
        browser.close()
    return out_path
