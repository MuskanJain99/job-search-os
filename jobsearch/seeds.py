"""Companies whose public job boards are polled (Greenhouse / Lever / Ashby / SmartRecruiters).

Tokens that don't exist simply return nothing (the feed call fails quietly), so this list can be generous.
Added to the `companies` table on every discover run (existing rows are left alone). Add more with
`insert into companies ...` or by pasting a job link to `jobsearch ingest`.
"""

from __future__ import annotations

SEEDS: list[tuple[str, str, str]] = [  # (name, ats, board token)
    # Greenhouse
    ("Stripe", "greenhouse", "stripe"), ("Airbnb", "greenhouse", "airbnb"), ("Databricks", "greenhouse", "databricks"),
    ("Cloudflare", "greenhouse", "cloudflare"), ("Coinbase", "greenhouse", "coinbase"), ("Figma", "greenhouse", "figma"),
    ("Datadog", "greenhouse", "datadog"), ("Elastic", "greenhouse", "elastic"), ("Twilio", "greenhouse", "twilio"),
    ("Okta", "greenhouse", "okta"), ("Pinterest", "greenhouse", "pinterest"), ("Lyft", "greenhouse", "lyft"),
    ("Reddit", "greenhouse", "reddit"), ("Robinhood", "greenhouse", "robinhood"), ("Instacart", "greenhouse", "instacart"),
    ("DoorDash", "greenhouse", "doordashusa"), ("Asana", "greenhouse", "asana"), ("Dropbox", "greenhouse", "dropbox"),
    ("Samsara", "greenhouse", "samsara"), ("Brex", "greenhouse", "brex"), ("Postman", "greenhouse", "postman"),
    ("BrowserStack", "greenhouse", "browserstack"), ("Freshworks", "greenhouse", "freshworks"), ("Chargebee", "greenhouse", "chargebee"),
    ("Druva", "greenhouse", "druva"), ("Groww", "greenhouse", "groww"), ("Razorpay", "greenhouse", "razorpaysoftwareprivatelimited"),
    ("PhonePe", "greenhouse", "phonepe"), ("Zeta", "greenhouse", "zeta"), ("ThoughtSpot", "greenhouse", "thoughtspot"),
    ("Nutanix", "greenhouse", "nutanix"), ("Pure Storage", "greenhouse", "purestorage"), ("Zscaler", "greenhouse", "zscaler"),
    ("Cohesity", "greenhouse", "cohesity"), ("Rubrik", "greenhouse", "rubrik"), ("New Relic", "greenhouse", "newrelic"),
    ("PagerDuty", "greenhouse", "pagerduty"), ("Fivetran", "greenhouse", "fivetran"), ("Confluent", "greenhouse", "confluent"),
    ("HashiCorp", "greenhouse", "hashicorp"), ("Snowflake", "greenhouse", "snowflake"), ("Splunk", "greenhouse", "splunk"),
    ("Sprinklr", "greenhouse", "sprinklr"), ("Amplitude", "greenhouse", "amplitude"), ("Mixpanel", "greenhouse", "mixpanel"),
    ("Segment", "greenhouse", "segment"), ("Gusto", "greenhouse", "gusto"), ("Carta", "greenhouse", "carta"),
    ("Toast", "greenhouse", "toast"), ("Squarespace", "greenhouse", "squarespace"), ("Webflow", "greenhouse", "webflow"),
    ("Zendesk", "greenhouse", "zendesk"), ("Workato", "greenhouse", "workato"), ("Dynatrace", "greenhouse", "dynatrace"),
    ("Qualtrics", "greenhouse", "qualtrics"), ("Medallia", "greenhouse", "medallia"), ("Navan", "greenhouse", "navan"),
    ("Intercom", "greenhouse", "intercom"), ("Monzo", "greenhouse", "monzo"), ("Revolut", "greenhouse", "revolut"),
    ("Checkout.com", "greenhouse", "checkoutcom"), ("GoCardless", "greenhouse", "gocardless"), ("Octopus Energy", "greenhouse", "octopusenergy"),
    ("Deliveroo", "greenhouse", "deliveroo"), ("Grab", "greenhouse", "grab"), ("Airwallex", "greenhouse", "airwallex"),
    ("Careem", "greenhouse", "careem"), ("Talabat", "greenhouse", "talabat"), ("Sea", "greenhouse", "seagroup"),
    # Lever
    ("Plaid", "lever", "plaid"), ("Palantir", "lever", "palantir"), ("Spotify", "lever", "spotify"), ("CRED", "lever", "cred"),
    ("Meesho", "lever", "meesho"), ("Zepto", "lever", "zepto"), ("Atlassian", "lever", "atlassian"), ("Paytm", "lever", "paytm"),
    ("Dream11", "lever", "dreamsports"), ("ShareChat", "lever", "sharechat"), ("Slice", "lever", "sliceit"),
    ("Jupiter", "lever", "jupiter"), ("Games24x7", "lever", "games24x7"), ("Swiggy", "lever", "swiggy"),
    ("Binance", "lever", "binance"), ("Crypto.com", "lever", "crypto"), ("Kraken", "lever", "kraken"),
    # Ashby
    ("OpenAI", "ashby", "openai"), ("Ramp", "ashby", "ramp"), ("Notion", "ashby", "notion"), ("Linear", "ashby", "linear"),
    ("Deel", "ashby", "deel"), ("Retool", "ashby", "retool"), ("Anthropic", "greenhouse", "anthropic"),
    ("Supabase", "ashby", "supabase"), ("Vercel", "greenhouse", "vercel"), ("Cohere", "ashby", "cohere"),
    ("Perplexity", "ashby", "perplexity"), ("Zapier", "ashby", "zapier"), ("Mercury", "ashby", "mercury"),
    ("Sarvam AI", "ashby", "sarvam"), ("Observe.AI", "ashby", "observeai"), ("Rippling", "ashby", "rippling"),
]
