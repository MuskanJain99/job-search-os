from pathlib import Path

from jobsearch import resume_pdf


class FakeConn:
    def __init__(self):
        self.row = None

    def execute(self, sql, params=()):
        if sql.startswith("insert into resume_files"):
            self.row = {"filename": params[1], "pdf": params[2]}
        self._last = sql
        return self

    def fetchone(self):
        return self.row if "from resume_files" in self._last else None


def test_default_resume_round_trip(tmp_path: Path):
    src = tmp_path / "Muskan_Nahata.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    conn = FakeConn()
    resume_pdf.store_default(conn, src)
    out = resume_pdf.resume_for(conn, {"id": "x", "resume_version_id": "rv"}, {}, tmp_path / "out")
    assert out.name == "Muskan_Nahata.pdf" and out.read_bytes() == b"%PDF-1.4 fake"


def test_rejects_non_pdf(tmp_path: Path):
    bad = tmp_path / "resume.docx"
    bad.write_bytes(b"PK..")
    try:
        resume_pdf.store_default(FakeConn(), bad)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
