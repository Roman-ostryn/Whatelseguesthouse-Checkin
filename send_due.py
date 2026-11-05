"""Create + email guest check-in links for eviivo stays due within N days."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import links
from config import link_mail_ready, link_public_ready, link_settings
from eviivo import EviivoError, eviivo_ready, load_upcoming_stays
from link_mail import send_checkin_link

LISBON = ZoneInfo("Europe/Lisbon")


def run_due_emails(*, dry_run: bool = False) -> dict:
    """
    For Whatelse bookings with check-in from today through CHECKIN_LINK_DAYS_BEFORE:
    create link (if needed) and email once. Covers the 15-day target and late bookings.
    """
    problems: list[str] = []
    for check in (eviivo_ready, link_public_ready, link_mail_ready):
        problem = check()
        if problem:
            problems.append(problem)
    if problems:
        result = {
            "ok": False,
            "error": " ".join(problems),
            "sent": [],
            "skipped": [],
            "failed": [],
            "no_email": [],
            "dry_run": dry_run,
        }
        links.save_auto_run(result)
        return result

    cfg = link_settings()
    days = int(cfg["days_before"])
    base = str(cfg["public_base_url"])
    today = datetime.now(LISBON).date()

    try:
        stays = load_upcoming_stays(days_ahead=max(days, 1) + 2)
    except EviivoError as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "sent": [],
            "skipped": [],
            "failed": [],
            "no_email": [],
            "dry_run": dry_run,
        }
        links.save_auto_run(result)
        return result

    sent: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    no_email: list[str] = []

    for stay in stays:
        label = _label(stay)
        checkin = str(stay.get("checkin") or "")
        if not _within_window(checkin, today, days):
            continue
        email = str(stay.get("email") or "").strip()
        if not email or "@" not in email:
            no_email.append(label)
            continue
        try:
            record = links.get_or_create(
                {
                    "reference": str(stay.get("reference") or ""),
                    "guest": str(stay.get("guest") or ""),
                    "email": email,
                    "checkin": checkin,
                    "checkout": str(stay.get("checkout") or ""),
                    "property_name": str(stay.get("property_name") or "Whatelse Guest House"),
                }
            )
        except Exception as exc:
            failed.append(f"{label}: {exc}")
            continue
        if record.get("filled_at"):
            skipped.append(f"{label}: already filled")
            continue
        if record.get("sent_at"):
            skipped.append(f"{label}: already emailed")
            continue
        if dry_run:
            sent.append(f"{label}: would email {email}")
            continue
        try:
            send_checkin_link(record, base_url=base)
            links.mark_sent(token=str(record.get("token") or ""))
            sent.append(f"{label} → {email}")
        except Exception as exc:
            failed.append(f"{label}: {exc}")

    result = {
        "ok": not failed,
        "error": "",
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "no_email": no_email,
        "dry_run": dry_run,
        "days_before": days,
        "today": today.isoformat(),
    }
    links.save_auto_run(result)
    return result


def result_message(result: dict) -> str:
    if result.get("error"):
        return str(result["error"])
    parts = [
        f"Auto send ({'dry-run' if result.get('dry_run') else 'live'}): "
        f"{len(result.get('sent') or [])} emailed, "
        f"{len(result.get('skipped') or [])} skipped, "
        f"{len(result.get('no_email') or [])} no email, "
        f"{len(result.get('failed') or [])} failed."
    ]
    if result.get("failed"):
        parts.append("Failures: " + "; ".join(result["failed"][:5]))
    return " ".join(parts)


def _within_window(checkin: str, today: date, days_before: int) -> bool:
    try:
        arrival = date.fromisoformat(str(checkin or "").strip())
    except ValueError:
        return False
    if arrival < today:
        return False
    return arrival <= today + timedelta(days=int(days_before))


def _label(stay: dict) -> str:
    guest = str(stay.get("guest") or "Guest")
    checkin = str(stay.get("checkin") or "")
    ref = str(stay.get("reference") or "")
    bit = f"{guest} ({checkin})"
    return f"{bit} [{ref}]" if ref else bit


if __name__ == "__main__":
    import json
    import sys

    dry = "--dry-run" in sys.argv
    out = run_due_emails(dry_run=dry)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(result_message(out))
    raise SystemExit(0 if out.get("ok") and not out.get("failed") else 1)
