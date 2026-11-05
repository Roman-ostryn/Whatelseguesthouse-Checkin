"""Email a guest check-in link via Resend (same pattern as invoice details)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from config import link_mail_ready, link_settings
from links import public_url

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def send_checkin_link(record: dict, *, base_url: str) -> None:
    problem = link_mail_ready()
    if problem:
        raise RuntimeError(problem)
    cfg = link_settings()
    email = str(record.get("email") or "").strip()
    if not email or "@" not in email:
        raise RuntimeError("Guest email is missing.")
    token = str(record.get("token") or "")
    link = public_url(token, base_url or str(cfg["public_base_url"]))
    if link.startswith("/"):
        raise RuntimeError("Set CHECKIN_PUBLIC_BASE_URL so the email contains a real https link.")
    guest = str(record.get("guest") or "Guest").strip() or "Guest"
    checkin = str(record.get("checkin") or "")
    checkout = str(record.get("checkout") or "")
    house = str(record.get("property_name") or "Whatelse Guest House")
    subject = f"{house} — online check-in"
    html_body = f"""
<p>Hello { _esc(guest) },</p>
<p>Please complete your check-in for <strong>{_esc(house)}</strong>
(check-in {_esc(checkin)}, check-out {_esc(checkout)}).</p>
<p><a href="{_esc(link)}">Open check-in form</a></p>
<p>It takes a few minutes. Thank you.</p>
<p>{_esc(house)}</p>
"""
    payload = {
        "from": cfg["resend_from"],
        "to": [email],
        "subject": subject,
        "html": html_body,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['resend_api_key']}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Resend HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Resend connection failed: {exc.reason}") from None


def _esc(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
