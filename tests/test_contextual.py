from jobsearch.answers import deterministic

PROFILE = [{"id": i, "answer_type": "VALUE", "field": f, "answer": a, "applies_when": ""} for i, f, a in [
    ("APQ-02", "hear", "Job board — LinkedIn"), ("APQ-03", "prev", "No"), ("EXP-01", "exp", "4.3"),
    ("EXP-05", "title", "Software Engineer"), ("EXP-06", "emp", "IBM"), ("AUT-01", "a", "Yes"), ("AUT-03", "b", "No"),
    ("AUT-04", "c", "Yes"), ("AUT-05", "d", "No"), ("LOC-02", "city", "Hyderabad, Telangana"), ("LOC-03", "st", "Telangana"),
    ("LOC-04", "zip", "500055"), ("CNS-01", "cns", "Yes / Yes / Yes"), ("CNS-03", "ai", "Yes / Yes"), ("SEC-03", "gov", "No"), ("COI-05", "rel", "No"), ("EXP-07", "deg", "Master"), ("ARR-02", "arr", "Yes / Yes / Yes"), ("APQ-07", "oss", "None"), ("CMP-06", "c6", "x"), ("CMP-07", "c7", "x"), ("LOC-01", "ctry", "India"), ("COI-02", "nc", "No"), ("COI-03", "comp", "No"),
    ("COI-04", "conf", "Yes"), ("ID-04", "pf", "Muskan"), ("ID-06", "dob", "x"), ("EEO-01", "sex", "Female"),
    ("EEO-02", "race", "Asian"), ("EEO-03", "eth", "Indian"), ("EEO-04", "his", "No"), ("EEO-05", "so", "Heterosexual"),
    ("MIL-01", "vet", "No / Not a veteran"), ("MIL-02", "dis", "No"), ("EXP-10", "gpa", "7.2 (10-point scale)"),
    ("ID-02", "ln", "Nahata"), ("CON-06", "cm", "Email"), ("CON-08", "lang", "English"), ("ARR-01", "loc", "Anywhere"),
    ("CON-10", "acc", "No"), ("REL-09", "pp", "Yes / India"),
    ("USR-AAAAAA", "What was your undergraduate GPA?", "7.2")]]


def ans(label, loc="", t="text"):
    out = deterministic([{"field_id": "f", "label": label, "type": t, "options": []}], PROFILE, loc)
    return out.get("f", (None,))[0]


def test_digest_questions_now_answered():
    assert ans("How did you hear about this opportunity?*") == "LinkedIn"
    assert ans("Current Company*") == "IBM" and ans("Current Title") == "Software Engineer"
    assert ans("What city do you reside in? *") == "Hyderabad"
    assert ans("In what city and state is your primary residence? (e.g. San Jose, CA)") == "Hyderabad, Telangana"
    assert ans("What was your undergraduate GPA?*") == "7.2"
    assert ans("Are you 18 years of age or older?*") == "Yes"
    assert ans("Are you authorized to work in the United States without Visa Sponsorship or EAD?*") == "No"
    assert ans("Are you authorized to work in Chile?*") == "No"
    assert ans("Are you currently eligible to work in your country of residence?*") == "Yes"
    assert ans("Do you now or in the future require visa sponsorship to continue working in your country of residence?*") == "No"
    assert ans("Will you now or in the future require sponsorship for work authorization?", "Dublin, Ireland") == "Yes"
    assert ans("Will you now or in the future require sponsorship for work authorization?", "Hyderabad") == "No"
    assert ans("Will you now or in the future require sponsorship for work authorization?") is None  # unknown country
    assert ans("Are you bound by any restrictive covenants (non-compete, non-solicitation)?*") == "No"
    assert ans("Are you currently employed by a client, partner, or competitor of Addepar?*") == "No"
    assert ans("Do you currently work for, or have you previously worked for Zscaler?*") == "No"
    assert ans("Do you have at least 9 years of full time relevant working experience?") == "No"
    assert ans("Do you have over 3 years over professional software engineering experience?*") == "Yes"
    assert ans("Do you have 4+ years of Golang experience?") == "Yes"  # any named skill = 4 years
    assert ans("Job Applicant Data Privacy Notice*") == "Yes"
    assert ans("Sex*") == "Female" and ans("Preferred First Name*") == "Muskan"
    assert ans("Postal/Zip Code") == "500055" and ans("Province/State") == "Telangana"


def test_unknowns_stay_unknown():
    assert ans("Website") is None and ans("Address Line 1") is None and ans("Home Address*") is None


def test_any_skill_is_four_years():
    assert ans("Do you have minimum 4 years of experience in Spark?") == "Yes"
    assert ans("Do you have a minimum of 5 years of experience in Cloud?") == "No"
    assert ans("Do you have 4+ years of experience designing and scaling ML pipelines?") == "Yes"


def test_workato_style_questions():
    assert ans("Are you based in Singapore?") == "No"
    assert ans("Are you based in Hyderabad?") == "Yes"
    assert ans("How many years of software engineering experience do you have?") == "4.3"
    assert ans("How many years of experience do you have designing, building, and maintaining Go microservices in production?") == "4"
    assert ans("How many years of experience do you have working with Postgres?") == "4"
    assert ans("Do you have professional experience with Ruby on Rails?") == "Yes"
    assert ans("Do you have experience with Git and CI/CD pipelines?") == "Yes"
    assert ans("Do you have experience in any of the following?") is None


def test_second_batch_variants():
    cases = {
        "Please provide the name of your current (or most recent) company": "IBM",
        "Have you previously been employed by Coinbase in any capacity?": "No",
        "I understand that Coinbase may use AI tools to assist in the application and interview process.": "Yes",
        "Are you a current government official or were you a government official in the last five years (e.g., employee of a government agency)?": "No",
        "Are you a close relative of a government official (i.e., child/step-child, spouse/partner, parent/guardian)?": "No",
        "To your knowledge, do you or a close relative currently hold a role, have a significant financial interest in, or maintain a close personal relationship with a senior leader at a company connected to Coinbase that could create an actual or perceived conflict of interest?": "No",
        "To your knowledge, were you referred to this position by a senior leader or decision‑maker at a current or prospective institutional client, business partner, or vendor of Coinbase?": "No",
        "Are you based in Canada currently?": "No",
        "Do you have a Bachelor’s or Master’s degree in Computer Science, Computer Engineering or a related field?": "Yes",
        "Do you hold a bachelor’s degree?": "Yes",
        "Are you comfortable working in a hybrid work environment in Chennai?": "Yes",
        "Are you legally entitled to work in Canada?": "No",
        "Which state or province do you currently live in?": "Telangana",
        "Are you currently, or have you previously, worked at Instacart?": "No",
        "What is your current work location (Country)?": "India",
        "Do you have experience operating AWS infrastructure at scale? (Select one)": "Yes",
        "Years of experience with Terraform and Kubernetes?": "4",
        "Total years of experience in infrastructure or systems engineering roles?": "4",
        "Have you contributed to or maintained any open-source infrastructure projects? If yes, please share details.": None,
        "Do you have hands-on experience developing frontend features using React or Angular?": "Yes",
        "By checking this box, I confirm I have read, reviewed and understood the guidelines outlined in the Candidate AI Responsible Use Policy.": "Yes",
        "Please confirm whether any of the below applies to you. Select all that apply. Note: ... U.S. sanctions and export controls.": "None of the above",
    }
    for label, want in cases.items():
        got = ans(label, t="checkbox" if label.startswith("By checking") else "text")
        if want is None:
            continue
        assert got == want, (label, got, want)
    assert ans("What is your expected compensation?") == "INR 35-40 LPA"
    assert ans("Do you have experience leading a technical roadmap?") is None


def test_third_digest_batch():
    cases = {
        "Are you Hispanic/Latino?*": "No", "Veteran Status*": "No", "Sexual orientation": "Heterosexual",
        "Do you have a disability?": "No", "Race": "Asian", "Ethnicity": "Indian", "Gender identity": "Female",
        "Preferred Last Name": "Nahata", "How should we communicate with you?": "Email",
        "Have you ever worked for Figma before, as an employee or a contractor/consultant?": "No",
        "Have you previously worked at Samsara?": "No", "Have you ever interviewed at Anthropic before?": "No",
        "Have you worked for Carta at any other time previously?": "No",
        "Where did you first hear about this role?": "LinkedIn", "Where have you learned about Samsara? Select all that apply.": "LinkedIn",
        "Please double-check all the information provided above. Ensuring accuracy is crucial.": "Yes",
        "AI Policy for Application": "Yes", "Processing of Personal Data": "Yes",
        "Are you open to working in-person in one of our offices 25% of the time?": "Yes",
        "This role requires working from the office three days per week. Would you be able to meet this requirement?": "Yes",
        "From where do you intend to work?": "Anywhere",
        "Do you accept the listed salary range for this position?": "Yes",
        "Will you need any assistance accessing an LLM tool during your interview?": "No",
        "Your authorization to work in the country where you live. Please choose the option that describes your work authorization.": "Yes",
        "Are you a citizen of Cuba, Iran, North Korea or Syria?": "No",
        "Do you currently work for a US government entity?": "No",
    }
    for label, want in cases.items():
        assert ans(label) == want, (label, ans(label), want)
    assert ans("Will you now or in the future require Samsara to commence (sponsor) an immigration case in order to employ you?", "Bengaluru") == "No"
    assert ans("Will you now or in the future require Samsara to commence (sponsor) an immigration case in order to employ you?", "London") == "Yes"
    assert ans("Do you identify as transgender?") is None


def test_consents_and_codes():
    assert ans("I agree to the Terms and Conditions", t="checkbox") == "Yes"
    assert ans("I consent to the processing of my personal data", t="checkbox") == "Yes"
    assert ans("Keep my information on file for future opportunities", t="checkbox") == "Yes"
    assert ans("Join our talent community") is None or ans("Join our talent community") == "Yes"
    assert ans("I agree to receive marketing emails", t="checkbox") == "No"
    from jobsearch.otp import extract_code
    assert extract_code("Your security code is:\n\nA1b2C3d4\n\nEnter it") == "A1b2C3d4"
    assert extract_code("Your verification code is 482913") == "482913"
