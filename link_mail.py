"""Email a guest check-in link via Resend (same pattern as invoice details)."""

from __future__ import annotations

import html
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
    subject, text_body, html_body = _message(guest, link, house, checkin, checkout)
    payload = {
        "from": cfg["resend_from"],
        "to": [email],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['resend_api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
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


def _message(
    guest: str, url: str, property_name: str, checkin: str, checkout: str
) -> tuple[str, str, str]:
    name = guest.strip() or "Guest"
    house = property_name.strip() or "Whatelse Guest House"
    subject = f"{house} — complete your online check-in"
    dates = ""
    if checkin and checkout:
        dates = f"Check-in {checkin} · Check-out {checkout}"
    elif checkin:
        dates = f"Check-in {checkin}"
    text = (
        f"{house}\n"
        "Fátima, Portugal\n\n"
        f"Olá {name},\n\n"
        "Antes da sua chegada, complete o check-in online. "
        "Demora apenas alguns minutos e ajuda-nos a cumprir as obrigações legais "
        "de alojamento em Portugal.\n\n"
    )
    if dates:
        text += f"Estadia: {dates}\n\n"
    text += (
        f"Completar o check-in online:\n{url}\n\n"
        "—\n\n"
        f"Hello {name},\n\n"
        "Before you arrive, please complete your online check-in. "
        "It only takes a few minutes and helps us meet Portugal’s guest registration requirements.\n\n"
    )
    if dates:
        text += f"Stay: {dates}\n\n"
    text += (
        f"Complete online check-in:\n{url}\n\n"
        f"{house}\n"
        "Rua da Moita de Baixo 81, 2495-651 Fátima, Portugal\n"
        "This message is only about your upcoming stay check-in.\n"
    )
    return subject, text, _html_message(name, url, house, checkin, checkout)


def _brand(property_name: str) -> tuple[str, str]:
    if property_name == "Whatelse Guest House":
        return "Whatelse", "GUEST HOUSE · FÁTIMA"
    return property_name or "Fátima", "FÁTIMA"


def _html_message(
    guest: str, url: str, property_name: str, checkin: str, checkout: str
) -> str:
    name = html.escape(guest)
    link = html.escape(url, quote=True)
    title, tag = _brand(property_name)
    title_html = html.escape(title)
    tag_html = html.escape(tag)
    house_html = html.escape(property_name or title)
    checkin_html = html.escape(checkin)
    checkout_html = html.escape(checkout)

    stay_block = ""
    if checkin or checkout:
        stay_block = f"""
<tr>
<td style="padding:0 32px 8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F7F3EC;border:1px solid #E4DDD2;">
<tr>
<td style="padding:16px 20px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1C2429;">
<div style="font-size:11px;letter-spacing:0.12em;color:#2F6F6A;font-weight:700;padding-bottom:8px;">YOUR STAY · A SUA ESTADIA</div>
{"Check-in: <strong>" + checkin_html + "</strong><br>" if checkin else ""}
{"Check-out: <strong>" + checkout_html + "</strong>" if checkout else ""}
</td>
</tr>
</table>
</td>
</tr>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{house_html} — Online check-in</title>
</head>
<body style="margin:0;padding:0;background:#F3F0EA;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">
Complete your online check-in for {house_html}. Completar o check-in online antes da chegada.
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F3F0EA;">
<tr>
<td align="center" style="padding:32px 16px;">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px;background:#FFFCF8;border:1px solid #E4DDD2;">
<tr>
<td style="background:#1A3A3A;padding:28px 32px 24px;">
<div style="font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.1;color:#F7F3EC;">{title_html}</div>
<div style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;letter-spacing:0.08em;color:#B8C9C6;padding-top:8px;">{tag_html}</div>
</td>
</tr>
<tr>
<td style="height:3px;background:#2F6F6A;font-size:0;line-height:0;">&nbsp;</td>
</tr>
<tr>
<td style="padding:28px 32px 8px;">
<div style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.12em;color:#2F6F6A;font-weight:700;">ONLINE CHECK-IN</div>
<p style="margin:12px 0 0;font-family:Georgia,'Times New Roman',serif;font-size:22px;line-height:1.3;color:#1A3A3A;">Hello {name},</p>
<p style="margin:12px 0 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#1C2429;">
We look forward to welcoming you to <strong>{house_html}</strong>.
Please complete your online check-in before arrival — it only takes a few minutes and helps us prepare your stay and meet Portugal’s guest registration requirements.
</p>
</td>
</tr>
{stay_block}
<tr>
<td style="padding:16px 32px 28px;">
<table role="presentation" cellpadding="0" cellspacing="0">
<tr>
<td style="background:#2F6F6A;border-radius:2px;">
<a href="{link}" style="display:inline-block;padding:14px 28px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;color:#FFFFFF;text-decoration:none;">Complete check-in</a>
</td>
</tr>
</table>
</td>
</tr>
<tr>
<td style="padding:0 32px;"><div style="border-top:1px solid #E4DDD2;font-size:0;line-height:0;">&nbsp;</div></td>
</tr>
<tr>
<td style="padding:24px 32px 8px;">
<div style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:0.12em;color:#2F6F6A;font-weight:700;">PORTUGUÊS</div>
<p style="margin:12px 0 0;font-family:Georgia,'Times New Roman',serif;font-size:22px;line-height:1.3;color:#1A3A3A;">Olá {name},</p>
<p style="margin:12px 0 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#1C2429;">
Temos o prazer de o(a) receber em <strong>{house_html}</strong>.
Complete o check-in online antes da chegada — demora apenas alguns minutos e permite-nos preparar a sua estadia e cumprir as obrigações legais de alojamento em Portugal.
</p>
</td>
</tr>
<tr>
<td style="padding:8px 32px 32px;">
<table role="presentation" cellpadding="0" cellspacing="0">
<tr>
<td style="background:#1A3A3A;border-radius:2px;">
<a href="{link}" style="display:inline-block;padding:14px 28px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;color:#F7F3EC;text-decoration:none;">Completar o check-in</a>
</td>
</tr>
</table>
</td>
</tr>
<tr>
<td style="background:#F7F3EC;padding:20px 32px;border-top:1px solid #E4DDD2;">
<div style="font-family:Georgia,'Times New Roman',serif;font-size:16px;color:#1A3A3A;">{house_html}</div>
<div style="font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;line-height:1.5;color:#6B7378;padding-top:4px;">
Rua da Moita de Baixo 81<br>
2495-651 Fátima, Portugal<br><br>
This email is only about your upcoming online check-in.<br>
Este email serve apenas para o check-in online da sua estadia.
</div>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>
"""
