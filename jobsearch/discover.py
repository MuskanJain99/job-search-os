"""discover → verify → score → decide → prepare (resume + answers). Leaves applications READY."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from . import answering, db, scoring, tailor, targeting
from .config import settings
from .log import get_logger
from .sources import aggregators, ats, search
from .sources.ats import Posting

log = get_logger("discover")

TITLE_QUERIES = ["Software Engineer", "Backend Engineer", "AI Engineer", "SDE II", "Senior Software Engineer",
                 "Platform Engineer", "Machine Learning Engineer", "Full Stack Engineer"]
REGIONS = {"India": ["Bengaluru", "Hyderabad", "India"], "Singapore": ["Singapore"], "UAE": ["Dubai"],
           "UK": ["London"], "US": ["United States"]}


def collect(conn, cfg) -> list[Posting]:
    out: list[Posting] = []
    # 1) Watched companies' public ATS boards
    companies = conn.execute(
        "select id, name, ats, ats_token from companies where active and ats in ('greenhouse','lever','ashby','smartrecruiters') "
        "order by priority, last_polled_at nulls first limit 60").fetchall()
    for c in companies:
        posts = ats.fetch_board(c["ats"], c["ats_token"])
        for p in posts:
            p.company_name = p.company_name or c["name"]
        out.extend(posts)
        conn.execute("update companies set last_polled_at=now() where id=%s", (c["id"],))
    # 2) Aggregators covering LinkedIn / Indeed / Glassdoor / Naukri listings
    for region, places in REGIONS.items():
        cc = aggregators.JSEARCH_COUNTRIES.get(region)
        for t in TITLE_QUERIES[:5]:
            if cc:
                out.extend(aggregators.jsearch(f"{t} in {places[0]}", cc))
            ac = aggregators.ADZUNA_COUNTRIES.get(region)
            if ac:
                out.extend(aggregators.adzuna(t, ac, where=places[0] if region == "India" else ""))
    # 3) Web search restricted to public ATS boards
    for q in search.build_queries(TITLE_QUERIES[:4], cfg["target_locations"])[:12]:
        for url in search.search_urls(q, limit=10):
            p = ats.find_posting(url)
            if p:
                out.append(p)
    return out


def _company(conn, name: str, p: Posting) -> str:
    norm = targeting.normalize(name) or "unknown"
    row = conn.execute("select id, ats from companies where normalized_name=%s", (norm,)).fetchone()
    if row:
        if p.ats in ats.FEEDS and row["ats"] in (None, "unknown", "custom"):
            conn.execute("update companies set ats=%s, ats_token=%s where id=%s", (p.ats, p.token, row["id"]))
        return str(row["id"])
    known_ats = p.ats if p.ats in ats.FEEDS else "unknown"
    row = conn.execute(
        "insert into companies (name, normalized_name, ats, ats_token, priority) values (%s,%s,%s,%s,3) "
        "on conflict (normalized_name) do update set name=excluded.name returning id",
        (name[:200], norm, known_ats, p.token if known_ats != "unknown" else None)).fetchone()
    return str(row["id"])


MAX_PER_COMPANY_PER_RUN = 3


def spread_by_company(postings: list[Posting]) -> list[Posting]:
    """Round-robin across companies so one employer with many openings can't use the whole daily cap."""
    groups: dict[str, list[Posting]] = {}
    for p in postings:
        groups.setdefault(targeting.normalize(p.company_name or p.token or ""), []).append(p)
    out: list[Posting] = []
    while groups:
        for k in list(groups):
            out.append(groups[k].pop(0))
            if not groups[k]:
                del groups[k]
    return out


def run() -> None:
    with db.run("discover_prepare") as r:
        conn = r.conn
        cfg = db.pipeline_config(conn)
        if cfg["paused"]:
            r.summary["skipped"] = "paused"
            return
        usage = db.daily_usage(conn)
        budget = max(0, cfg["max_new_jobs_per_day"] - (usage["new_jobs_today"] or 0))
        evidence = db.resume_evidence(conn)
        skeleton = db.resume_skeleton(conn)
        profile = db.profile_answers(conn)
        sch12 = next((p["answer"] for p in profile if p["id"] == "SCH-12"), "")

        postings = spread_by_company(collect(conn, cfg))
        r.summary["collected"] = len(postings)
        seen: set[str] = set()
        per_company: dict[str, int] = {}
        for p in postings:
            if per_company.get(targeting.normalize(p.company_name or p.token or ""), 0) >= MAX_PER_COMPANY_PER_RUN:
                r.bump("skip_company_cap")
                continue
            if budget <= 0:
                r.partial("daily new-job cap reached")
                break
            ok, why = targeting.title_ok(p.title, cfg["exclude_keywords"])
            if not ok:
                r.bump(f"skip_{why.split(':')[0]}")
                continue
            loc = targeting.classify_location(p.location, p.remote, p.description)
            if not loc.eligible:
                r.bump("skip_location")
                continue
            p = aggregators.resolve(p)
            company = p.company_name or p.token
            key = targeting.dedupe_key(targeting.normalize(company), f"{p.ats}:{p.job_id}" if p.ats in ats.FEEDS else None,
                                       p.title, p.location)
            if key in seen or conn.execute("select 1 from jobs where dedupe_key=%s or url=%s", (key, p.url)).fetchone():
                r.bump("skip_seen")
                continue
            seen.add(key)
            per_company[targeting.normalize(company)] = per_company.get(targeting.normalize(company), 0) + 1
            if len(p.description) < 400:
                text = search.fetch_page_text(p.url)
                if len(text) > len(p.description):
                    p.description = text
            if len(p.description) < 200:
                r.bump("skip_unverifiable")
                continue
            try:
                process(r, p, company, loc, cfg, evidence, skeleton, profile, sch12)
                budget -= 1
            except Exception as exc:  # one bad posting must not stop the run
                r.bump("errors")
                r.audit("job.error", "jobs", None, kind=type(exc).__name__, msg=str(exc)[:300])


def process(r: db.Run, p: Posting, company: str, loc, cfg, evidence, skeleton, profile, sch12) -> None:
    conn = r.conn
    company_id = _company(conn, company, p)
    h = targeting.jd_hash(p.description)
    key = targeting.dedupe_key(targeting.normalize(company), f"{p.ats}:{p.job_id}" if p.ats in ats.FEEDS else None,
                               p.title, p.location)
    job = conn.execute(
        """insert into jobs (company_id, source, ats_job_id, url, title, normalized_title, location, country,
               remote_policy, india_eligible, jd_text, jd_hash, status, posted_at, last_verified_at, dedupe_key)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,now(),%s)
           on conflict (dedupe_key) do update set last_seen_at=now() returning id""",
        (company_id, p.extra.get("source") or p.ats, p.job_id, p.url, p.title[:300], targeting.normalize(p.title),
         p.location[:300], loc.country, loc.remote_policy, loc.india_eligible, p.description, h, p.posted_at, key),
    ).fetchone()
    job_id = str(job["id"])
    conn.execute("insert into job_snapshots (job_id, jd_hash, jd_text) values (%s,%s,%s)", (job_id, h, p.description))
    r.bump("new_jobs")

    fit = scoring.score_job(p.title, company, p.location, p.description, evidence)
    conn.execute(
        "insert into fit_scores (job_id, score, verdict, matched_claims, gaps, rationale, prompt_version) values (%s,%s,%s,%s,%s,%s,%s)",
        (job_id, fit.score, fit.verdict, fit.matched_claims, Jsonb([g.model_dump() for g in fit.gaps]),
         fit.rationale[:1000], scoring.PROMPT_VERSION))
    go, reason = targeting.decide(
        cfg.get("apply_policy", "ALL_ELIGIBLE"), fit.verdict, fit.score, cfg["min_fit_score_to_apply"], loc,
        fit.min_years_required, (cfg.get("min_years_ok", 2), cfg.get("max_years_ok", 8)),
        fit.sponsorship_or_relocation, scoring.shift_ok(fit.shift_or_hours, loc.remote_policy, sch12),
        fit.skip_reason)
    r.audit("job.scored", "jobs", job_id, score=fit.score, decision=reason)
    if not go:
        r.bump(f"skip_{reason.split('_')[0]}")
        return
    prepare(r, job_id, p, company, fit.score, evidence, skeleton, profile)


def prepare(r: db.Run, job_id: str, p: Posting, company: str, score: int, evidence, skeleton, profile) -> str:
    conn = r.conn
    channel = aggregators.apply_channel(p.apply_url or p.url)
    priority = 1 if score >= 80 else 2 if score >= 70 else 3 if score >= 55 else 4
    app = conn.execute(
        """insert into applications (job_id, status, priority, discovered_via, channel, next_action)
           values (%s,'PREPARING',%s,%s,%s,null) on conflict (job_id) do nothing returning id""",
        (job_id, priority, p.extra.get("source") or p.ats, channel)).fetchone()
    if not app:
        return ""
    app_id = str(app["id"])
    job = {"title": p.title, "company": company, "jd_text": p.description}
    content, used, emphasis = tailor.tailor(job, evidence, skeleton)
    rv = conn.execute(
        "insert into resume_versions (job_id, label, claim_ids, generator_version, summary, content) values (%s,%s,%s,%s,%s,%s) returning id",
        (job_id, f"{company} — {p.title}", used, tailor.GENERATOR_VERSION, emphasis[:500], Jsonb(content))).fetchone()
    conn.execute("update applications set resume_version_id=%s where id=%s", (rv["id"], app_id))

    stopped = False
    if p.ats == "greenhouse":
        fields = [_gh_field(q) for q in ats.greenhouse_questions(p.token, p.job_id)]
        fields = [f for f in fields if f]
        if fields:
            res = answering.resolve_with_owner(r, app_id, fields, profile, evidence, p.title, company)
            if res is None:
                stopped = True  # alerted, waited, recorded — move on; a late reply re-queues it
            else:
                for a in res.provenance:
                    conn.execute("insert into app_answers (application_id, question_text, profile_id, answer, basis) values (%s,%s,%s,%s,%s)",
                                 (app_id, a["question_text"], a["profile_id"], a["answer"], a["basis"]))
    if not stopped:
        # Cloud handles public employer forms; the laptop helper handles everything else
        # (signed-in boards, external "Apply" links, Workday/company sites).
        nxt = "submit" if channel in aggregators.CLOUD_SUBMIT else "laptop_submit"
        db.set_application_status(r, app_id, "READY", next_action=nxt)
        r.bump(f"ready_{nxt}")
    return app_id


def _gh_field(q: dict) -> dict | None:
    flds = q.get("fields") or []
    if not flds:
        return None
    f = flds[0]
    opts = [v.get("label") for v in (f.get("values") or []) if v.get("label")]
    kind = {"input_file": "file", "textarea": "textarea", "multi_value_single_select": "select",
            "multi_value_multi_select": "multiselect", "input_text": "text", "input_hidden": "hidden"}.get(f.get("type"), "text")
    if kind == "hidden":
        return None
    return {"field_id": f.get("name"), "label": q.get("label", ""), "type": kind,
            "required": bool(q.get("required")), "options": opts[:40]}


if __name__ == "__main__":
    log.info("dry_run=%s", settings.dry_run)
    run()
