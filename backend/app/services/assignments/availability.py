from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

PLATFORM_TIMEZONE = timezone(timedelta(hours=8))
DATE = re.compile(r"(?:(?P<year>\d{4})[-/.年])?(?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?(?:(?:\s+|T)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?")


def assignment_deadline(item, now=None):
    now = now or datetime.now(PLATFORM_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=PLATFORM_TIMEZONE)
    text = str(item.get("deadline_text") or item.get("deadlineText") or "")
    matches = list(DATE.finditer(text))
    if not matches:
        return None
    end = matches[-1].groupdict()
    month, day = int(end["month"]), int(end["day"])
    year = int(end["year"]) if end["year"] else None
    if year is None:
        semester = re.match(r"(\d{4})-(\d{4})-([12])", str(item.get("semester") or ""))
        if matches[0].group("year"):
            year = int(matches[0].group("year")) + (1 if int(matches[0].group("month")) > month else 0)
        elif semester:
            start, finish, term = map(int, semester.groups())
            year = finish if term == 2 or month < 8 else start
        else:
            academic_year = item.get("academic_year") or item.get("academicYear")
            year = int(academic_year) if str(academic_year).isdigit() and int(academic_year) >= 2000 else now.year
            if len(matches) > 1 and int(matches[0].group("month")) > month:
                year += 1 if academic_year or now.month >= 8 else 0
    try:
        iso = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$", text.strip())
        if iso:
            return datetime.fromisoformat(iso.group().replace("Z", "+00:00"))
        return datetime(year, month, day, int(end["hour"] or 23), int(end["minute"] or 59), int(end["second"] or 59), 999999, tzinfo=PLATFORM_TIMEZONE)
    except ValueError:
        return None


def assignment_expired(item, now=None):
    now = now or datetime.now(PLATFORM_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=PLATFORM_TIMEZONE)
    deadline = assignment_deadline(item, now)
    return deadline is not None and deadline < now
