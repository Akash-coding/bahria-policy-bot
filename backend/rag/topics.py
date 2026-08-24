from __future__ import annotations

import re

TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "attendance": ("attendance", "present", "presence", "absent", "absentee", "shortfall"),
    "examination": ("exam", "exams", "examination", "midterm", "finals", "invigil"),
    "leave": ("leave", "leaves", "medical leave", "casual leave", "sick leave"),
    "fee": ("fee", "fees", "tuition", "refund", "challan"),
    "admission": ("admission", "admissions", "eligibility", "enrolment", "enrollment"),
    "discipline": ("discipline", "misconduct", "cheating", "plagiarism", "rusticat", "expel"),
    "harassment": ("harassment", "hepe", "sexual harassment"),
    "hostel": ("hostel", "dormitory", "resident"),
    "probation": ("probation", "cgpa", "gpa", "academic warning"),
    "semester": ("semester", "freeze", "defer", "withdrawal"),
    "grade": ("grade", "grading", "gpa", "transcript"),
    "conduct": ("conduct", "ethics", "code of conduct"),
    "scholarship": ("scholarship", "financial aid", "stipend"),
}

_HEADING = re.compile(
    r"^(?:"
    r"(?:chapter|section|part|article|clause)\s+[\w.]+(?:\s*[:.-]\s*.+)?"
    r"|\d+(?:\.\d+){0,3}[.:)]\s+\S.+"
    r"|[A-Z][A-Z0-9 ,/&'\-]{10,}"
    r")\s*$"
)

_TOKEN = re.compile(r"[a-z0-9]{3,}")


def heading_in(text: str) -> str:
    for line in (text or "").splitlines()[:10]:
        stripped = line.strip()
        if _HEADING.match(stripped):
            return stripped[:160]
    return ""


def extract_topics(*texts: str) -> set[str]:
    blob = " ".join(texts).lower()
    found: set[str] = set()
    for topic, aliases in TOPIC_ALIASES.items():
        if any(alias in blob for alias in aliases):
            found.add(topic)
    return found


def query_terms(text: str) -> set[str]:
    return {token for token in _TOKEN.findall((text or "").lower()) if len(token) > 2}
