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
    "scholarship": (
        "scholarship",
        "scholarships",
        "financial aid",
        "stipend",
        "merit award",
        "fee concession",
    ),
}

_GPA_TERM = re.compile(r"\bc?gpa\b", re.I)
_AID_HINT = re.compile(
    r"\b(benefit|benefits|scholarship|scholarships|merit|concession|"
    r"discount|stipend|financial aid|eligible|eligibility)\b",
    re.I,
)
_ADMISSION_HINT = re.compile(
    r"\b(admission|admissions|apply|enrol|enroll|want to get)\b",
    re.I,
)

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


def expanded_topics(text: str) -> set[str]:
    """Topics to search for, including implied links such as CGPA + admission → scholarship."""
    topics = extract_topics(text)
    if _GPA_TERM.search(text or "") and (
        "admission" in topics
        or "fee" in topics
        or _AID_HINT.search(text or "")
        or _ADMISSION_HINT.search(text or "")
    ):
        topics.add("scholarship")
    return topics


def query_expansion(text: str) -> str:
    """Extra search words so compound questions still retrieve the related policy."""
    if not _GPA_TERM.search(text or ""):
        return ""
    if not (
        "admission" in extract_topics(text)
        or _AID_HINT.search(text or "")
        or _ADMISSION_HINT.search(text or "")
    ):
        return ""
    if re.search(r"\bscholarships?\b", text or "", re.I):
        return ""
    return (
        "scholarship merit award financial aid fee concession "
        "CGPA eligibility admission requirements"
    )
