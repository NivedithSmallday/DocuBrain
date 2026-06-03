import re


SENSITIVE_MEMORY_REFUSAL = (
    "I'm not able to store personal sensitive information. Please refer to "
    "your HR system or secure profile for this data."
)


_SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:date of birth|birth date|dob|born on)\b", re.IGNORECASE),
    re.compile(r"\b(?:ssn|social security|national id|passport)\b", re.IGNORECASE),
    re.compile(r"\b(?:salary|compensation|medical|health|password)\b", re.IGNORECASE),
    # Date-like values only flagged when near a PII keyword (avoids false
    # positives on version numbers like "2/4/64").
    re.compile(
        r"\b(?:born|dob|birth)\b.{0,30}\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        re.IGNORECASE,
    ),
)


def contains_sensitive_memory_data(memory: str) -> bool:
    return any(pattern.search(memory) for pattern in _SENSITIVE_PATTERNS)
