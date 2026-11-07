"""Read Whatelse bookings from eviivo (same API as invoice automation)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import load_settings

LISBON = ZoneInfo("Europe/Lisbon")
PROPERTY_ID = "Whatelseguesthouse2495651"
PROPERTY_NAME = "Whatelse Guest House"
# eviivo rejects a check-in window longer than about a month.
CHUNK_SPAN_DAYS = 30


class EviivoError(RuntimeError):
    pass


def eviivo_ready() -> str:
    """Empty when eviivo credentials exist."""
    cfg = eviivo_settings()
    if not cfg["client_id"] or not cfg["client_secret"]:
        return "Add EVIIVO_CLIENT_ID and EVIIVO_CLIENT_SECRET to .env for auto guest links."
    return ""


def eviivo_settings() -> dict[str, str]:
    load_settings()
    return {
        "client_id": str(os.getenv("EVIIVO_CLIENT_ID", "")).strip(),
        "client_secret": str(os.getenv("EVIIVO_CLIENT_SECRET", "")).strip(),
        "token_url": str(
            os.getenv("EVIIVO_TOKEN_URL", "https://auth.eviivo.com/api/connect/token")
        ).strip()
        or "https://auth.eviivo.com/api/connect/token",
        "api_base": str(os.getenv("EVIIVO_API_BASE", "https://io.eviivo.com")).strip().rstrip("/")
        or "https://io.eviivo.com",
        "property_id": str(os.getenv("EVIIVO_PROPERTY_SHORTNAME", PROPERTY_ID)).strip()
        or PROPERTY_ID,
        "property_name": str(os.getenv("EVIIVO_PROPERTY_NAME", PROPERTY_NAME)).strip()
        or PROPERTY_NAME,
    }


def load_month_stays(year: int, month: int) -> list[dict]:
    """Whatelse bookings that overlap one calendar month (for INE)."""
    from calendar import monthrange

    start = date(year, month, 1)
    # Include stays that checked in before the month but slept inside it.
    lookback = start - timedelta(days=40)
    last_day = monthrange(year, month)[1]
    end = date(year, month, last_day)
    return _load_range(lookback, end)


def load_upcoming_stays(*, days_ahead: int = 20) -> list[dict]:
    """Whatelse stays with check-in from today through days_ahead (Lisbon)."""
    today = datetime.now(LISBON).date()
    last = today + timedelta(days=max(0, int(days_ahead)))
    return _load_range(today, last)


def _load_range(start: date, end: date) -> list[dict]:
    cfg = eviivo_settings()
    problem = eviivo_ready()
    if problem:
        raise EviivoError(problem)
    token = _token(cfg)
    stays: list[dict] = []
    seen: set[str] = set()
    for window_start, window_end in _windows(start, end):
        query = urllib.parse.urlencode(
            {"checkInFrom": window_start.isoformat(), "checkInTo": window_end.isoformat()}
        )
        path = f"/pms/v2/property/{cfg['property_id']}/bookings?{query}"
        status, body = _get(cfg["api_base"], path, token, cfg["client_id"])
        if status != 200 or not isinstance(body, dict):
            detail = body if isinstance(body, str) else json.dumps(body)[:300]
            raise EviivoError(f"eviivo bookings failed (HTTP {status}): {detail}")
        for item in body.get("Bookings") or []:
            parsed = _parse_stay(item, cfg["property_id"], cfg["property_name"])
            if parsed is None:
                continue
            key = str(parsed.get("reference") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            stays.append(parsed)
    stays.sort(key=lambda s: (str(s.get("checkin") or ""), str(s.get("guest") or "").lower()))
    return stays


def _windows(start: date, last: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= last:
        end = min(cursor + timedelta(days=CHUNK_SPAN_DAYS), last)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return windows


def _parse_stay(item: object, property_id: str, property_name: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    booking = item.get("Booking") or {}
    if booking.get("Cancelled"):
        return None
    guests = item.get("Guests") or []
    primary = next(
        (g for g in guests if isinstance(g, dict) and g.get("PrimaryGuest")),
        None,
    )
    if primary is None and guests and isinstance(guests[0], dict):
        primary = guests[0]
    primary = primary or {}
    name = " ".join(
        part.strip()
        for part in (str(primary.get("FirstName") or ""), str(primary.get("Surname") or ""))
        if part and str(part).strip()
    )
    if not name:
        return None
    email = str(primary.get("Email") or "").strip()
    if email.lower() in {"placeholder@no-email.com", "no-email"}:
        email = ""
    checkin = str(booking.get("CheckinDate") or "")[:10]
    checkout = str(booking.get("CheckoutDate") or "")[:10]
    reference = str(booking.get("BookingReference") or "").strip()
    if not reference or not checkin:
        return None
    address = primary.get("Address") if isinstance(primary, dict) else None
    country = ""
    if isinstance(address, dict):
        country = str(address.get("CountryCode") or "").strip().upper()
    try:
        adults = int(booking.get("NumberOfAdults") or 0)
    except (TypeError, ValueError):
        adults = 0
    try:
        children = int(booking.get("NumberOfChildren") or 0)
    except (TypeError, ValueError):
        children = 0
    guests_count = adults + children
    if guests_count < 1:
        guests_count = 1
    return {
        "reference": reference,
        "property_id": property_id,
        "property_name": property_name,
        "guest": name,
        "email": email,
        "checkin": checkin,
        "checkout": checkout,
        "country": country,
        "guests": guests_count,
    }


def _token(cfg: dict[str, str]) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        cfg["token_url"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    status, payload = _read(request)
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise EviivoError(f"eviivo login failed (HTTP {status}).")
    return str(payload["access_token"])


def _get(api_base: str, path: str, token: str, client_id: str) -> tuple[int, object]:
    request = urllib.request.Request(
        api_base + path,
        headers={
            "Authorization": f"bearer {token}",
            "X-Auth-ClientId": client_id,
            "accept": "application/json",
        },
        method="GET",
    )
    return _read(request)


def _read(request: urllib.request.Request) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise EviivoError(f"Could not reach eviivo: {exc.reason}") from exc
    if not raw.strip():
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw[:300]
