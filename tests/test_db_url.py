from jobsearch.db import normalize_url


def test_raw_at_sign_in_password():
    out = normalize_url("postgresql://postgres.abc:pa@ss@001@aws-0-x.pooler.supabase.com:5432/postgres")
    assert out == "postgresql://postgres.abc:pa%40ss%40001@aws-0-x.pooler.supabase.com:5432/postgres"


def test_plain_and_encoded_untouched():
    plain = "postgresql://u:secret@host:5432/postgres"
    assert normalize_url(plain) == plain
    enc = "postgresql://u:a%40b@host:5432/postgres"
    assert normalize_url(enc) == enc
