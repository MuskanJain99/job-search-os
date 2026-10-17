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


DEFAULT_NAME = "default"


class NoDefaultResume(RuntimeError):
    pass


def store_default(conn, path: Path) -> str:
    """Save her own resume PDF to the database so cloud runs can attach it."""
    import hashlib

    data = Path(path).read_bytes()
    if not data.startswith(b"%PDF"):
        raise ValueError("not a PDF file")
    digest = hashlib.sha256(data).hexdigest()
    conn.execute(
        "insert into resume_files (name, filename, pdf, sha256) values (%s,%s,%s,%s) "
        "on conflict (name) do update set filename=excluded.filename, pdf=excluded.pdf, sha256=excluded.sha256, uploaded_at=now()",
        (DEFAULT_NAME, Path(path).name, data, digest))
    return digest


def default_pdf(conn, out_dir: Path) -> Path:
    row = conn.execute("select filename, pdf from resume_files where name=%s", (DEFAULT_NAME,)).fetchone()
    if not row:
        raise NoDefaultResume("default resume not uploaded — run: jobsearch resume set-default <path to PDF>")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (row["filename"] or "Muskan_Nahata.pdf")
    out.write_bytes(bytes(row["pdf"]))
    return out


def resume_for(conn, app: dict, header: dict, out_dir: Path) -> Path:
    """The file to attach for this application: her default resume unless RESUME_MODE=tailored."""
    from .config import settings

    if settings.resume_mode != "tailored" or not app.get("resume_version_id"):
        return default_pdf(conn, out_dir)
    row = conn.execute("select content from resume_versions where id=%s", (app["resume_version_id"],)).fetchone()
    if not row:
        return default_pdf(conn, out_dir)
    return render_pdf(row["content"], header, out_dir / safe_name("Muskan_Nahata", app.get("company") or "", app.get("title") or ""))


def ensure_default(r) -> bool:
    """Called at the start of submit runs: without her resume nothing can be attached, so stop and tell her once a day."""
    from .config import settings

    if settings.resume_mode == "tailored":
        return True
    if r.conn.execute("select 1 from resume_files where name=%s", (DEFAULT_NAME,)).fetchone():
        return True
    from . import alerts

    r.partial("default resume not uploaded")
    alerts.alert(r, "pipeline_broken", "no-default-resume",
                 "Job search paused submitting: upload your resume once with `jobsearch resume set-default <file.pdf>` on your Mac.")
    return False
