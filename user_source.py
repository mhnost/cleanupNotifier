"""
User record source layer for per-user email classification (rebuilt
2026-07-23 -- see CONTEXT.md and inactivity_logic.py).

Reads the CURRENT CSV dump only (config.CURRENT_USER_SOURCE_CSV_PATH) --
classification judges each account's current inactivity, it doesn't
need the previous snapshot the way the deactivation-diff pipeline does.

This module stays split in two, same as before:

  1. fetch_raw_user_records() -- reads the CSV dump and yields plain dicts.
  2. to_candidate_user() -- pure transform from a raw dict to a
     CandidateUser, with no file/backend dependency at all. This is where
     the last_logon/created fallback and domain_admin exclusion logic
     live, and it's fully unit-testable independent of the CSV file.

Expected raw record shape (a plain dict per user), field names matching
the CSV columns as given by the data owner (created, disabled,
display_name, dn, domain, domain_admin, email, last_logon,
password_last_set, username -- only the fields this module uses are
listed below; the rest are read from the CSV but not currently needed):
    {
        "username":      <string, e.g. sAMAccountName>,
        "email":         <string or None>,
        "last_logon":    <datetime or None>,
        "created":       <datetime>,
        "domain_admin":  <bool>,
        "disabled":      <bool>,
        # service-account marking: no dedicated field -- every service
        # account is also flagged domain_admin (confirmed by PMO), so
        # domain_admin alone is a sufficient exclusion signal for both.
        # See config.SERVICE_ACCOUNT_MARKING_CONFIRMED.
    }
"""

import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

import config


@dataclass(frozen=True)
class CandidateUser:
    username: Optional[str]
    display_name: Optional[str]
    email: Optional[str]
    days_since_reference: Optional[int]
    used_created_fallback: bool
    is_excluded: bool
    last_logon: Optional[datetime]
    reference_date: Optional[datetime]
    domain: Optional[str]


def _parse_datetime(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_bool(value: str) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


def fetch_raw_user_records() -> Iterator[dict]:
    """Read the current user data CSV dump and yield one raw record dict
    per row.

    Already-disabled accounts are filtered out here -- an already-
    disabled account needs no warning or deactivation-notification
    email; it's already deactivated.

    One malformed row (e.g. an unparseable last_logon/created date)
    must not crash the whole run: a bad row is skipped
    and logged with its line number and username (if readable) instead
    of raising out of this generator and losing every account after it.
    """
    config.require_current_snapshot_path()
    with open(config.CURRENT_USER_SOURCE_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_number, row in enumerate(reader, start=2):  # header is line 1
            if _parse_bool(row.get(config.DISABLED_FIELD)):
                continue
            try:
                yield {
                    config.IDENTIFIER_FIELD: row.get(config.IDENTIFIER_FIELD),
                    config.DISPLAY_NAME_FIELD: row.get(config.DISPLAY_NAME_FIELD),
                    config.EMAIL_FIELD: row.get(config.EMAIL_FIELD),
                    config.LAST_LOGON_FIELD: _parse_datetime(row.get(config.LAST_LOGON_FIELD)),
                    config.CREATED_FIELD: _parse_datetime(row.get(config.CREATED_FIELD)),
                    config.DOMAIN_FIELD: row.get(config.DOMAIN_FIELD),
                    config.DOMAIN_ADMIN_FIELD: _parse_bool(row.get(config.DOMAIN_ADMIN_FIELD)),
                }
            except Exception as exc:
                print(
                    f"WARNING: skipping malformed row {line_number} in "
                    f"{config.CURRENT_USER_SOURCE_CSV_PATH} "
                    f"(username={row.get(config.IDENTIFIER_FIELD)!r}): {exc}",
                    file=sys.stderr,
                )


def _days_since(dt: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if dt is None:
        return None
    now = now or datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


def to_candidate_user(raw: dict, now: Optional[datetime] = None) -> CandidateUser:
    """Pure transform: raw record dict -> CandidateUser.

    Reference date for inactivity is last_logon if present, otherwise
    created -- i.e. an account that was created 6+ months ago and has
    never logged in is treated as inactive for that whole period, not
    exempted just because it never had a first logon.
    """
    last_logon = raw.get(config.LAST_LOGON_FIELD)
    created = raw.get(config.CREATED_FIELD)

    if last_logon is not None:
        reference_dt = last_logon
        used_created_fallback = False
    else:
        reference_dt = created
        used_created_fallback = created is not None

    is_domain_admin = bool(raw.get(config.DOMAIN_ADMIN_FIELD, False))
    # domain_admin is the only exclusion signal, and it's sufficient:
    # every service account is also flagged domain_admin (confirmed by
    # PMO), even though not every domain_admin is a service account. Since
    # both groups are excluded identically, that asymmetry doesn't matter.
    is_excluded = is_domain_admin

    return CandidateUser(
        username=raw.get(config.IDENTIFIER_FIELD),
        display_name=raw.get(config.DISPLAY_NAME_FIELD),
        email=raw.get(config.EMAIL_FIELD),
        days_since_reference=_days_since(reference_dt, now=now),
        used_created_fallback=used_created_fallback,
        is_excluded=is_excluded,
        last_logon=last_logon,
        reference_date=reference_dt,
        domain=raw.get(config.DOMAIN_FIELD),
    )
