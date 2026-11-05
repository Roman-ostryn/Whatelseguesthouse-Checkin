"""Save guest check-ins locally (SIBA/INE status flags)."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from paths import app_root

LISBON = ZoneInfo("Europe/Lisbon")
ROOT = app_root()
DATA_DIR = ROOT / "data"
PATH = DATA_DIR / "checkins.json"
_LOCK = threading.Lock()


def save_checkin(
    fields: dict[str, str],
    *,
    source: str = "hotel",
    link_token: str = "",
    booking_reference: str = "",
) -> dict:
    """Append one validated check-in. Returns the stored record."""
    now = datetime.now(LISBON).isoformat(timespec="seconds")
    record = {
        "id": secrets.token_hex(8),
        "created_at": now,
        "siba_sent_at": "",
        "ine_included": False,
        "source": str(source or "hotel"),
        "link_token": str(link_token or ""),
        "booking_reference": str(booking_reference or ""),
        "fields": dict(fields),
    }
    with _LOCK:
        data = _read()
        data["checkins"].append(record)
        _write(data)
    return dict(record)


def list_checkins(limit: int = 100) -> list[dict]:
    with _LOCK:
        rows = list(_read().get("checkins") or [])
    rows.reverse()
    return rows[: max(0, limit)]


def get_checkin(checkin_id: str) -> dict | None:
    key = str(checkin_id or "").strip()
    if not key:
        return None
    with _LOCK:
        for row in _read().get("checkins") or []:
            if str(row.get("id") or "") == key:
                return dict(row)
    return None


def update_checkin(checkin_id: str, fields: dict[str, str]) -> dict | None:
    """Replace guest fields; keep id, SIBA/INE flags, and source metadata."""
    key = str(checkin_id or "").strip()
    if not key:
        return None
    now = datetime.now(LISBON).isoformat(timespec="seconds")
    with _LOCK:
        data = _read()
        for row in data.get("checkins") or []:
            if str(row.get("id") or "") != key:
                continue
            row["fields"] = dict(fields)
            row["updated_at"] = now
            _write(data)
            return dict(row)
    return None


def delete_checkin(checkin_id: str) -> bool:
    key = str(checkin_id or "").strip()
    if not key:
        return False
    with _LOCK:
        data = _read()
        rows = list(data.get("checkins") or [])
        kept = [row for row in rows if str(row.get("id") or "") != key]
        if len(kept) == len(rows):
            return False
        data["checkins"] = kept
        _write(data)
        return True


def count_checkins() -> int:
    with _LOCK:
        return len(_read().get("checkins") or [])


def next_siba_file_number() -> int:
    """Next SIBA Numero_Ficheiro (1..99999)."""
    with _LOCK:
        data = _read()
        current = int(data.get("siba_file_number") or 0)
        nxt = current + 1
        if nxt > 99999:
            nxt = 1
        data["siba_file_number"] = nxt
        _write(data)
        return nxt


def mark_siba_sent(checkin_id: str, *, file_number: int, result: str = "0") -> bool:
    key = str(checkin_id or "").strip()
    if not key:
        return False
    now = datetime.now(LISBON).isoformat(timespec="seconds")
    with _LOCK:
        data = _read()
        for row in data.get("checkins") or []:
            if str(row.get("id") or "") != key:
                continue
            row["siba_sent_at"] = now
            row["siba_file_number"] = int(file_number)
            row["siba_result"] = str(result or "0")
            _write(data)
            return True
    return False


def mark_ine_included(checkin_ids: list[str], *, year: int, month: int) -> int:
    """Mark guests counted in an IPHH month as included. Returns how many updated."""
    keys = {str(x).strip() for x in checkin_ids if str(x).strip()}
    if not keys:
        return 0
    stamp = f"{year:04d}-{month:02d}"
    now = datetime.now(LISBON).isoformat(timespec="seconds")
    updated = 0
    with _LOCK:
        data = _read()
        for row in data.get("checkins") or []:
            if str(row.get("id") or "") not in keys:
                continue
            row["ine_included"] = True
            row["ine_month"] = stamp
            row["ine_marked_at"] = now
            updated += 1
        if updated:
            _write(data)
    return updated


def _read() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PATH.is_file():
        return {"checkins": [], "siba_file_number": 0}
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"checkins": [], "siba_file_number": 0}
    if not isinstance(raw, dict):
        return {"checkins": [], "siba_file_number": 0}
    rows = raw.get("checkins")
    if not isinstance(rows, list):
        rows = []
    number = raw.get("siba_file_number") or 0
    try:
        number = int(number)
    except (TypeError, ValueError):
        number = 0
    return {"checkins": rows, "siba_file_number": number}


def _write(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)
