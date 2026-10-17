"""Logging that is safe for PUBLIC GitHub Actions logs.

Rules: log counts, IDs and status words. Never log names, email addresses, phone numbers,
message bodies, profile answers or secrets. A redaction filter is a last line of defence.
"""

from __future__ import annotations

import logging
import re
import sys

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s-]{8,}\d)(?!\w)")
_TOKEN = re.compile(r"(sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{10,}|ya29\.[\w-]+|secret_[A-Za-z0-9]{10,})")


def redact(text: str) -> str:
    text = _EMAIL.sub("<email>", text)
    text = _TOKEN.sub("<secret>", text)
    text = _PHONE.sub("<phone>", text)
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


def get_logger(name: str = "jobsearch") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(_RedactFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
