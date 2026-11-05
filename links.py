"""Guest check-in links (token URL). One link per booking reference."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from paths import app_root

LISBON = ZoneInfo("Europe/Lisbon")
ROOT = app_root()
DATA_DIR = ROOT / "data"
PATH = DATA_DIR / "guest_links.json"
AUTO_RUN_PATH = DATA_DIR / "auto_link_run.json"
_LOCK = threading.Lock()


def get_or_create(stay: dict) -> dict:
    """Stable link per booking reference."""
    reference = str(stay.get("reference") or "").strip()
    if not reference:
        reference = "guest-" + secrets.token_hex(8)
    with _LOCK:
        data = _read()
        record = data["by_reference"].get(reference)
        if record is None:
            token = secrets.token_urlsafe(18)
            record = {
                "token": token,
                "reference": reference,
                "guest": stay.get("guest") or "",
                "email": stay.get("email") or "",
                "checkin": stay.get("checkin") or "",
                "checkout": stay.get("checkout") or "",
                "property_name": stay.get("property_name") or "Whatelse Guest House",
                "sent_at": "",
                "filled_at": "",
                "checkin_id": "",
                "siba_auto": "",
            }
            data["by_reference"][reference] = record
            data["by_token"][token] = reference
        else:
            for key in ("guest", "email", "checkin", "checkout", "property_name"):
                incoming = stay.get(key)
                if incoming:
                    record[key] = incoming
        _write(data)
        return dict(record)


def find_token(token: str) -> dict | None:
    key = str(token or "").strip()
    if not key:
        return None
    with _LOCK:
        data = _read()
        reference = data["by_token"].get(key)
        record = data["by_reference"].get(reference or "")
        return dict(record) if record else None


def mark_sent(token: str = "", reference: str = "") -> bool:
    with _LOCK:
        data = _read()
        if token:
            reference = data["by_token"].get(token) or reference
        record = data["by_reference"].get(str(reference or "").strip())
        if record is None:
            return False
        record["sent_at"] = datetime.now(LISBON).isoformat(timespec="seconds")
        _write(data)
        return True


def mark_filled(
    token: str,
    *,
    checkin_id: str,
    siba_auto: str = "",
) -> bool:
    with _LOCK:
        data = _read()
        reference = data["by_token"].get(token)
        record = data["by_reference"].get(reference or "")
        if record is None:
            return False
        record["filled_at"] = datetime.now(LISBON).isoformat(timespec="seconds")
        record["checkin_id"] = checkin_id
        record["siba_auto"] = siba_auto
        _write(data)
        return True


def list_links(limit: int = 100) -> list[dict]:
    with _LOCK:
        rows = list((_read().get("by_reference") or {}).values())
    rows.sort(key=lambda r: str(r.get("checkin") or ""), reverse=True)
    return rows[: max(0, limit)]


def save_auto_run(result: dict) -> None:
    """Remember the last automatic email job for the /links screen."""
    payload = {
        "at": datetime.now(LISBON).isoformat(timespec="seconds"),
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or ""),
        "sent": list(result.get("sent") or []),
        "skipped": list(result.get("skipped") or []),
        "failed": list(result.get("failed") or []),
        "no_email": list(result.get("no_email") or []),
        "dry_run": bool(result.get("dry_run")),
        "days_before": result.get("days_before"),
        "today": result.get("today"),
    }
    with _LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = AUTO_RUN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(AUTO_RUN_PATH)


def last_auto_run() -> dict | None:
    with _LOCK:
        if not AUTO_RUN_PATH.is_file():
            return None
        try:
            raw = json.loads(AUTO_RUN_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return raw if isinstance(raw, dict) else None


def delete_link(token: str = "", reference: str = "") -> bool:
    """Remove a guest link by token or booking reference."""
    with _LOCK:
        data = _read()
        ref = str(reference or "").strip()
        tok = str(token or "").strip()
        if tok and not ref:
            ref = str(data["by_token"].get(tok) or "")
        if not ref:
            return False
        record = data["by_reference"].pop(ref, None)
        if record is None:
            return False
        tok = str(record.get("token") or tok)
        if tok:
            data["by_token"].pop(tok, None)
        _write(data)
        return True


def due_for_link_day(checkin: str, today: date | None = None, *, days_before: int = 15) -> bool:
    """True when check-in is exactly `days_before` days from today (Lisbon)."""
    day = today or datetime.now(LISBON).date()
    try:
        arrival = date.fromisoformat(str(checkin or "").strip())
    except ValueError:
        return False
    return arrival == day + timedelta(days=int(days_before))


def public_url(token: str, base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if not base:
        return f"/g/{token}"
    return f"{base}/g/{token}"


def _read() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PATH.is_file():
        return {"by_reference": {}, "by_token": {}}
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"by_reference": {}, "by_token": {}}
    if not isinstance(raw, dict):
        return {"by_reference": {}, "by_token": {}}
    by_ref = raw.get("by_reference")
    by_tok = raw.get("by_token")
    if not isinstance(by_ref, dict):
        by_ref = {}
    if not isinstance(by_tok, dict):
        by_tok = {}
    return {"by_reference": by_ref, "by_token": by_tok}


def _write(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)
