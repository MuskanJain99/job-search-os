# job-search-os

An automated, **evidence-grounded** job search pipeline. It finds software-engineering roles across the big job
boards, tailors a one-page resume per job from a bank of verified achievements, applies, asks for referrals,
tracks every reply, and only interrupts its owner when a human is genuinely needed.

It runs on a schedule in **GitHub Actions** (no server, no laptop required), with an optional **laptop helper**
for the sites that need a signed-in browser.

```
                ┌──────────── GitHub Actions (cloud, every day) ─────────────┐
 JSearch ──┐    │ discover → verify → score → decide → tailor resume → answers│
 Adzuna  ──┼──▶ │ submit (Greenhouse / Lever / Ashby / SmartRecruiters / …)   │──▶ Supabase (system of record)
 ATS feeds─┤    │ outreach (Gmail) · inbox (Gmail + Calendar) · Notion · digest│        ▲      │
 Search  ──┘    └──────────────────────────────────────────────────────────────┘        │      ▼
                ┌──────────── Laptop helper (macOS launchd, when awake) ──────┐        │   Notion
                │ LinkedIn (Easy Apply + external Apply), Naukri, Instahyre,  │────────┘
                │ Indeed, Workday & company portals (auto account creation)    │
                └──────────────────────────────────────────────────────────────┘
          any step that needs a human → alert (email + push + SMS) → wait 2 min → continue or move on
```

## What makes it trustworthy

- **No invented experience.** The LLM only chooses and rephrases. Code enforces that every resume bullet cites
  real `claim_id`s from the evidence bank for the right employer, that every number in a bullet exists in those
  claims, that skills come only from cited claims, and that employers/titles/dates come from a fixed skeleton.
  (`jobsearch/tailor.py`, tested in `tests/test_truthfulness.py`.)
- **Form answers come from an approved answer sheet** (`profile_answers`). `ASK_ME` / `NEVER_ANSWER_AUTOMATICALLY`
  fields and unknown required questions are never guessed — the owner is asked.
- **Human-in-the-loop with a timeout.** CAPTCHAs, verification codes, sign-ins, unknown questions: alert →
  wait (`HUMAN_WAIT_SECONDS`, default 120 s) → continue if handled, otherwise record and move on. Late email
  replies are picked up by the inbox stage and re-queue the job. Anti-bot checks are never bypassed.
- **Safe public logs.** Logs contain counts and IDs only; a redaction filter strips emails, phones and keys.
- **Everything outbound is off by default** (`DRY_RUN=true`, `SUBMIT_ENABLED=false`, `EMAIL_ENABLED=false`).

## Stages

| Command | What it does | Schedule (IST) |
|---|---|---|
| `jobsearch discover` | Collect from JSearch (Google for Jobs: LinkedIn/Indeed/Glassdoor listings), Adzuna, watched companies' public ATS boards and web search; resolve each listing to the employer's own posting; filter (mid-level SWE/backend/AI/platform, eligible location, shift rule); score; tailor resume; pre-answer forms | every 3 h |
| `jobsearch submit` | Fill and submit public employer forms with a rendered PDF resume; capture confirmation | every 2 h, 08–22 |
| `jobsearch outreach` | Find recruiters/engineers with a public source; email only published addresses; 2 follow-ups max; stop on reply | hourly, 09:30–18:30 |
| `jobsearch inbox` | Classify job emails; confirm/reject/interview/offer; hold interview slots on Calendar; apply late answers | every 30 min |
| `jobsearch notion` | Mirror application state to Notion | after every run |
| `jobsearch digest` | Weekly email: funnel, open questions, jobs waiting for the laptop | Sunday 19:00 |
| `jobsearch ingest <url>` | Paste-a-link: put a job at the front of the queue | on demand (workflow input) |
| `jobsearch laptop auto|assist|login` | Laptop helper (see below) | every 2 h when the Mac is awake |

## Apply policy

`pipeline_config.apply_policy = ALL_ELIGIBLE`: apply to every mid-level software/backend/AI/platform/full-stack
engineering role in an eligible location. The fit score only orders the queue. Skips: non-engineering, intern/junior,
staff/principal/lead/manager, years required outside 2–8, ineligible location, "no sponsorship" abroad, shift rule,
security clearance, non-English, closed.

## Setup

1. **Database** — a Supabase project with the job-search-os schema. Add `DATABASE_URL` (session pooler string).
2. **LLM** — set repository *variables* `LLM_PROVIDER` (`openai_compatible` | `openai` | `anthropic`), `LLM_MODEL`,
   optionally `LLM_MODEL_FAST`, `LLM_BASE_URL`; add one secret, `LLM_API_KEY`. The default setup uses Google Gemini's free tier
   (key from aistudio.google.com/apikey); Groq and OpenRouter free models also work.
3. **Job sources** (any subset) — `JSEARCH_API_KEY` (RapidAPI), `ADZUNA_APP_ID` + `ADZUNA_APP_KEY`, `FIRECRAWL_API_KEY`.
4. **Gmail + Calendar** — run `scripts/google_oauth.py` once; add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_REFRESH_TOKEN`, `OWNER_EMAIL`.
5. **Alerts** — `NTFY_TOPIC` (install the ntfy app on your phone and subscribe to a long random topic);
   optional SMS via `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `OWNER_PHONE`.
6. **Notion** (optional) — `NOTION_TOKEN` secret, `NOTION_APPLICATIONS_DB` variable; share the database with the integration.
7. **Go live gradually** — variables `DRY_RUN=false`, then `EMAIL_ENABLED=true`, then `SUBMIT_ENABLED=true`.

### Laptop helper (macOS)

```bash
git clone <this repo> && cd job-search-os
./scripts/install_macos_agent.sh          # venv, env file, launchd job (every 2 h + at login)
jobsearch laptop login                    # sign in once to LinkedIn, Naukri, Instahyre, Indeed… in a dedicated profile
jobsearch laptop assist                   # any time: finish jobs that are waiting on a human step
```

Employer portals that require an account (Workday, iCIMS, Taleo…) are signed up for automatically: unique random
password stored only in Supabase Vault (`portal_<host>`), required consents only, verification email read from Gmail.

## Development

```bash
pip install -e ".[dev]"
ruff check . && pytest
```

## License

MIT
