"""Command line entry point: `jobsearch <stage>` / `python -m jobsearch <stage>`."""

from __future__ import annotations

import argparse
import sys

from .log import get_logger

log = get_logger("cli")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobsearch", description="Automated, evidence-grounded job search pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in [
        ("discover", "find, verify, score and prepare jobs"),
        ("submit", "submit READY applications on public employer forms (cloud)"),
        ("outreach", "find contacts, send referral emails and follow-ups"),
        ("inbox", "classify Gmail threads, book interviews, alert when needed"),
        ("notion", "sync application state to Notion"),
        ("digest", "send the weekly summary email"),
    ]:
        sub.add_parser(name, help=help_)
    p_ing = sub.add_parser("ingest", help="add a pasted job link to the front of the queue")
    p_ing.add_argument("url")
    p_lap = sub.add_parser("laptop", help="laptop helper (signed-in Chrome profile)")
    p_lap.add_argument("mode", choices=["auto", "assist", "login"])
    p_res = sub.add_parser("resume", help="manage the resume that gets attached")
    p_res.add_argument("action", choices=["set-default"])
    p_res.add_argument("path")
    p_foc = sub.add_parser("focus", help="do-not-disturb: no windows/pushes for a while, e.g. `focus 2h` or `focus off`")
    p_foc.add_argument("spec")
    args = ap.parse_args(argv)

    if args.cmd == "discover":
        from . import discover

        discover.run()
    elif args.cmd == "submit":
        from . import submit

        submit.run()
    elif args.cmd == "outreach":
        from . import outreach

        outreach.run()
    elif args.cmd == "inbox":
        from . import inbox

        inbox.run()
    elif args.cmd == "notion":
        from . import notion_sync

        notion_sync.run()
    elif args.cmd == "digest":
        from . import digest

        digest.run()
    elif args.cmd == "ingest":
        from . import ingest

        ingest.run(args.url)
    elif args.cmd == "laptop":
        from . import laptop

        {"auto": laptop.auto, "assist": laptop.assist, "login": laptop.login}[args.mode]()
    elif args.cmd == "focus":
        from . import focus

        print(focus.set_manual(args.spec))
    elif args.cmd == "resume":
        from . import db, resume_pdf

        with db.run("resume_upload") as r:
            digest = resume_pdf.store_default(r.conn, args.path)
            r.audit("resume.set_default", "resume_files", "default", sha256=digest[:16])
        print("Default resume saved. Every application will attach this file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
