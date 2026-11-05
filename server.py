"""Guest check-in form. Saves locally; SIBA send + INE monthly summary."""

from __future__ import annotations

import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import links
import store
from config import (
    cron_authorized,
    cron_secret,
    ine_ready,
    ine_settings,
    link_mail_ready,
    link_public_ready,
    link_settings,
)
from eviivo import eviivo_ready
from fields import COMMON_COUNTRIES, DOC_TYPES, FIELD_SPEC
from ine import (
    MonthReport,
    build_month_report,
    default_report_month,
    parse_year_month,
    report_to_csv,
)
from link_mail import send_checkin_link
from mrz import MrzError, parse_mrz
from send_due import result_message, run_due_emails
from siba import SibaError, is_foreign_guest, send_checkin

# Local default; Railway sets PORT and we listen on 0.0.0.0.
HOST = str(os.getenv("HOST", "127.0.0.1")).strip() or "127.0.0.1"
PORT = int(str(os.getenv("PORT", "8787")).strip() or "8787")
_KNOWN_COUNTRIES = {code for code, _name in COMMON_COUNTRIES if code != "OTHER"}


def make_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host or HOST, port if port is not None else PORT), _Handler)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/checkin"}:
            self._html(200, _form_page({}, error="", ok_message=""))
            return
        if path == "/list":
            self._html(200, _list_page(store.list_checkins()))
            return
        if path == "/links":
            params = parse_qs(parsed.query, keep_blank_values=True)
            filt = str((params.get("filter") or ["all"])[-1] or "all").lower()
            q = str((params.get("q") or [""])[-1] or "").strip()
            self._html(
                200,
                _links_page(message="", ok=True, filter_key=filt, search=q),
            )
            return
        if path in {"/internal/cron/send-due-links", "/cron/send-due-links"}:
            params = parse_qs(parsed.query, keep_blank_values=True)
            auth = self.headers.get("Authorization") or self.headers.get("X-Cron-Secret") or ""
            secret = (params.get("secret") or [""])[-1]
            if not cron_authorized(auth, secret):
                self._html(401, _shell("Unauthorized", "<p>Missing or invalid CRON_SECRET.</p>"))
                return
            dry = (params.get("dry_run") or [""])[-1].lower() in {"1", "true", "yes"}
            result = run_due_emails(dry_run=dry)
            raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if result.get("ok") else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path.startswith("/g/"):
            token = path[len("/g/") :].strip("/")
            record = links.find_token(token)
            if record is None:
                self._html(404, _shell("Not found", "<p>This check-in link is invalid.</p>"))
                return
            if record.get("filled_at"):
                self._html(200, _guest_done_page(record, already=True))
                return
            values = {
                "checkin_date": str(record.get("checkin") or ""),
                "checkout_date": str(record.get("checkout") or ""),
                "email": str(record.get("email") or ""),
            }
            self._html(200, _guest_form_page(token, record, values, error=""))
            return
        if path == "/ine":
            year, month, message, ok = _ine_month_from_query(parsed.query)
            report = build_month_report(store.list_checkins(limit=10_000), year, month)
            self._html(200, _ine_page(report, message=message, ok=ok))
            return
        if path == "/ine.csv":
            year, month, _msg, _ok = _ine_month_from_query(parsed.query)
            report = build_month_report(store.list_checkins(limit=10_000), year, month)
            self._csv(report)
            return
        if path.startswith("/checkin/") and path.endswith("/edit"):
            checkin_id = path[len("/checkin/") : -len("/edit")].strip("/")
            record = store.get_checkin(checkin_id)
            if record is None:
                self._html(404, _shell("Not found", "<p>Check-in not found.</p>"))
                return
            self._html(200, _form_page(_fields_for_form(dict(record.get("fields") or {})), edit_id=checkin_id, record=record))
            return
        if path.startswith("/checkin/"):
            record = store.get_checkin(path[len("/checkin/") :].strip("/"))
            if record is None:
                self._html(404, _shell("Not found", "<p>Check-in not found.</p>"))
                return
            self._html(200, _detail_page(record, message="", ok=True))
            return
        self._html(404, _shell("Not found", "<p>Page not found.</p>"))

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        data = {key: (vals[-1] if vals else "") for key, vals in parse_qs(raw, keep_blank_values=True).items()}
        if path == "/checkin/mrz":
            values, err, ok_msg = _apply_mrz(data)
            status = 400 if err and not ok_msg else 200
            self._html(status, _form_page(values, error=err, ok_message=ok_msg, mrz_text=str(data.get("mrz") or "")))
            return
        if path.startswith("/g/") and path.endswith("/mrz"):
            token = path[len("/g/") : -len("/mrz")].strip("/")
            link = links.find_token(token)
            if link is None or link.get("filled_at"):
                self._html(404, _shell("Not found", "<p>This check-in link is invalid.</p>"))
                return
            values, err, ok_msg = _apply_mrz(data)
            values["checkin_date"] = str(link.get("checkin") or values.get("checkin_date") or "")
            values["checkout_date"] = str(link.get("checkout") or values.get("checkout_date") or "")
            values["email"] = str(link.get("email") or values.get("email") or "")
            status = 400 if err and not ok_msg else 200
            self._html(
                status,
                _guest_form_page(
                    token,
                    link,
                    values,
                    error=err,
                    ok_message=ok_msg,
                    mrz_text=str(data.get("mrz") or ""),
                ),
            )
            return
        if path.startswith("/g/"):
            token = path[len("/g/") :].strip("/")
            link = links.find_token(token)
            if link is None:
                self._html(404, _shell("Not found", "<p>This check-in link is invalid.</p>"))
                return
            if link.get("filled_at"):
                self._html(200, _guest_done_page(link, already=True))
                return
            data = dict(data)
            data["checkin_date"] = str(link.get("checkin") or data.get("checkin_date") or "")
            data["checkout_date"] = str(link.get("checkout") or data.get("checkout_date") or "")
            cleaned, errors = _validate(data)
            if errors:
                self._html(400, _guest_form_page(token, link, data, error=" ".join(errors)))
                return
            record, siba_note = _save_and_maybe_siba(
                cleaned,
                source="guest_link",
                link_token=token,
                booking_reference=str(link.get("reference") or ""),
            )
            links.mark_filled(token, checkin_id=str(record.get("id") or ""), siba_auto=siba_note)
            print(f"Guest link filled {token} -> {record['id']} siba={siba_note}", flush=True)
            self._html(200, _guest_done_page(links.find_token(token) or link, already=False, siba_note=siba_note))
            return
        if path.startswith("/checkin/") and path.endswith("/send-siba"):
            checkin_id = path[len("/checkin/") : -len("/send-siba")].strip("/")
            record, message, ok = _send_siba(checkin_id)
            if record is None:
                self._html(404, _shell("Not found", "<p>Check-in not found.</p>"))
                return
            self._html(200 if ok else 400, _detail_page(record, message=message, ok=ok))
            return
        if path.startswith("/checkin/") and path.endswith("/remove"):
            checkin_id = path[len("/checkin/") : -len("/remove")].strip("/")
            if store.delete_checkin(checkin_id):
                self._html(
                    200,
                    _list_page(store.list_checkins(), message="Check-in removed.", ok=True),
                )
            else:
                self._html(
                    404,
                    _list_page(store.list_checkins(), message="Check-in not found.", ok=False),
                )
            return
        if path.startswith("/checkin/") and path.endswith("/edit/mrz"):
            checkin_id = path[len("/checkin/") : -len("/edit/mrz")].strip("/")
            record = store.get_checkin(checkin_id)
            if record is None:
                self._html(404, _shell("Not found", "<p>Check-in not found.</p>"))
                return
            values, err, ok_msg = _apply_mrz(data)
            status = 400 if err and not ok_msg else 200
            self._html(
                status,
                _form_page(
                    values,
                    error=err,
                    ok_message=ok_msg,
                    mrz_text=str(data.get("mrz") or ""),
                    edit_id=checkin_id,
                    record=record,
                ),
            )
            return
        if path.startswith("/checkin/") and path.endswith("/edit"):
            checkin_id = path[len("/checkin/") : -len("/edit")].strip("/")
            existing = store.get_checkin(checkin_id)
            if existing is None:
                self._html(404, _shell("Not found", "<p>Check-in not found.</p>"))
                return
            cleaned, errors = _validate(data)
            if errors:
                self._html(
                    400,
                    _form_page(
                        data,
                        error=" ".join(errors),
                        edit_id=checkin_id,
                        record=existing,
                    ),
                )
                return
            record = store.update_checkin(checkin_id, cleaned)
            if record is None:
                self._html(404, _shell("Not found", "<p>Check-in not found.</p>"))
                return
            note = "Check-in updated."
            if record.get("siba_sent_at"):
                note += " Local data only — AIMA was not changed. Send to SIBA again only if needed."
            self._html(200, _detail_page(record, message=note, ok=True))
            return
        if path == "/links/create":
            stay = {
                "reference": str(data.get("reference") or "").strip(),
                "guest": str(data.get("guest") or "").strip(),
                "email": str(data.get("email") or "").strip(),
                "checkin": str(data.get("checkin") or "").strip(),
                "checkout": str(data.get("checkout") or "").strip(),
                "property_name": "Whatelse Guest House",
            }
            if not stay["checkin"] or not stay["checkout"]:
                self._html(400, _links_page(message="Check-in and check-out dates are required.", ok=False))
                return
            record = links.get_or_create(stay)
            cfg = link_settings()
            url = links.public_url(str(record["token"]), str(cfg["public_base_url"]))
            msg = f"Link ready: {url}"
            self._html(200, _links_page(message=msg, ok=True, highlight=str(record["token"])))
            return
        if path == "/links/email":
            token = str(data.get("token") or "").strip()
            record = links.find_token(token)
            if record is None:
                self._html(400, _links_page(message="Link not found.", ok=False))
                return
            try:
                cfg = link_settings()
                send_checkin_link(record, base_url=str(cfg["public_base_url"]))
                links.mark_sent(token=token)
                self._html(200, _links_page(message=f"Email sent to {record.get('email')}.", ok=True))
            except Exception as exc:
                self._html(400, _links_page(message=str(exc), ok=False))
            return
        if path == "/links/remove":
            token = str(data.get("token") or "").strip()
            if links.delete_link(token=token):
                self._html(200, _links_page(message="Guest link removed.", ok=True))
            else:
                self._html(400, _links_page(message="Link not found.", ok=False))
            return
        if path == "/links/auto-run":
            result = run_due_emails(dry_run=False)
            self._html(
                200 if result.get("ok") else 400,
                _links_page(message=result_message(result), ok=bool(result.get("ok"))),
            )
            return
        if path in {"/internal/cron/send-due-links", "/cron/send-due-links"}:
            auth = self.headers.get("Authorization") or self.headers.get("X-Cron-Secret") or ""
            if not cron_authorized(auth, str(data.get("secret") or "")):
                self._html(401, _shell("Unauthorized", "<p>Missing or invalid CRON_SECRET.</p>"))
                return
            result = run_due_emails(dry_run=str(data.get("dry_run") or "") in {"1", "true", "yes"})
            raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if result.get("ok") else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/ine/mark":
            parsed = parse_year_month(str(data.get("month") or ""))
            if parsed is None:
                y, m = default_report_month()
                report = build_month_report(store.list_checkins(limit=10_000), y, m)
                self._html(400, _ine_page(report, message="Invalid month.", ok=False))
                return
            year, month = parsed
            report = build_month_report(store.list_checkins(limit=10_000), year, month)
            n = store.mark_ine_included(report.guest_ids, year=year, month=month)
            msg = f"Marked {n} guest(s) as included in INE {report.label}."
            report = build_month_report(store.list_checkins(limit=10_000), year, month)
            self._html(200, _ine_page(report, message=msg, ok=True))
            return
        if path not in {"/", "/checkin"}:
            self._html(404, _shell("Not found", "<p>Page not found.</p>"))
            return
        cleaned, errors = _validate(data)
        if errors:
            self._html(400, _form_page(data, error=" ".join(errors), ok_message=""))
            return
        record, _siba = _save_and_maybe_siba(cleaned, source="hotel", auto_siba=False)
        print(f"Saved check-in {record['id']}.", flush=True)
        self._html(200, _thanks_page(record))

    def log_message(self, format: str, *args: object) -> None:
        print(f"  {args[0]}" if args else format % args, flush=True)

    def _html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _csv(self, report: MonthReport) -> None:
        payload = report_to_csv(report).encode("utf-8")
        filename = f"iphh-{report.label}.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _validate(data: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    out: dict[str, str] = {}
    errors: list[str] = []

    def take(name: str) -> str:
        return " ".join(str(data.get(name) or "").split()).strip()

    for name, required, label in FIELD_SPEC:
        value = take(name)
        if name in {"nationality", "document_country", "residence_country"}:
            other = take(f"{name}_other").upper()
            if value == "OTHER" or (other and value in {"", "OTHER"}):
                value = other
            value = value.upper()
        if name == "document_type":
            value = value.upper()
        if name == "document_number":
            value = re.sub(r"[^0-9A-Za-z]", "", value).upper()
        if name in {"surname", "given_name"}:
            value = value.upper()
        if required and not value:
            errors.append(f"{label} is required.")
        out[name] = value

    for key in ("birth_date", "checkin_date", "checkout_date"):
        if out[key] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", out[key]):
            errors.append(f"Use YYYY-MM-DD for {_label(key)}.")

    if out["checkout_date"] and out["checkin_date"] and out["checkout_date"] < out["checkin_date"]:
        errors.append("Check-out must be on or after check-in.")

    for key in ("nationality", "document_country", "residence_country"):
        if out[key] and not re.fullmatch(r"[A-Z]{3}", out[key]):
            errors.append(f"{_label(key)} must be a 3-letter country code (e.g. PRT).")

    if out["document_type"] and out["document_type"] not in {"P", "B", "O"}:
        errors.append("Document type must be Passport, Identity card, or Other.")

    return out, errors


def _label(name: str) -> str:
    for key, _req, label in FIELD_SPEC:
        if key == name:
            return label
    return name


def _esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def _country_options(selected: str) -> str:
    parts: list[str] = ['<option value="">— select —</option>']
    for code, name in COMMON_COUNTRIES:
        mark = " selected" if selected == code or (code != "OTHER" and selected == code) else ""
        if selected and selected not in {c for c, _ in COMMON_COUNTRIES} and code == "OTHER":
            mark = " selected"
        parts.append(f'<option value="{_esc(code)}"{mark}>{_esc(name)} ({_esc(code)})</option>')
    return "\n".join(parts)


def _doc_options(selected: str) -> str:
    parts: list[str] = ['<option value="">— select —</option>']
    for code, name in DOC_TYPES:
        mark = " selected" if selected == code else ""
        parts.append(f'<option value="{_esc(code)}"{mark}>{_esc(name)}</option>')
    return "\n".join(parts)


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(title)}</title>
  <style>
    :root {{
      --ink: #1c1917;
      --muted: #57534e;
      --line: #d6d3d1;
      --bg: #fafaf9;
      --card: #ffffff;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.45;
    }}
    main {{
      max-width: 44rem;
      margin: 0 auto;
      padding: 1.5rem 1rem 3rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 0.45rem 0.35rem;
      text-align: left;
    }}
    th.num, td.num {{ text-align: right; }}
    tfoot td {{ font-weight: 650; }}
    h1 {{
      font-size: 1.5rem;
      font-weight: 650;
      margin: 0 0 0.35rem;
    }}
    .sub {{ color: var(--muted); margin: 0 0 1.25rem; font-size: 0.95rem; }}
    form, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1.1rem 1rem 1.25rem;
    }}
    label {{
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      margin: 0.85rem 0 0.3rem;
    }}
    label .opt {{ font-weight: 500; color: var(--muted); }}
    input, select, textarea {{
      width: 100%;
      padding: 0.55rem 0.65rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      font: inherit;
      background: #fff;
    }}
    textarea {{ min-height: 4rem; resize: vertical; }}
    .row {{ display: grid; gap: 0.75rem; }}
    @media (min-width: 560px) {{
      .row.two {{ grid-template-columns: 1fr 1fr; }}
    }}
    .hint {{ font-size: 0.8rem; color: var(--muted); margin: 0.25rem 0 0; }}
    .error {{
      background: #fef2f2;
      border: 1px solid #fecaca;
      color: #991b1b;
      padding: 0.75rem 0.85rem;
      border-radius: 8px;
      margin-bottom: 1rem;
    }}
    .ok {{
      background: #ecfdf5;
      border: 1px solid #a7f3d0;
      color: #065f46;
      padding: 0.75rem 0.85rem;
      border-radius: 8px;
      margin-bottom: 1rem;
    }}
    button {{
      margin-top: 1.25rem;
      width: 100%;
      border: 0;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }}
    button.secondary {{
      background: #44403c;
    }}
    button.inline {{
      margin-top: 0.65rem;
      width: auto;
      padding: 0.55rem 0.9rem;
    }}
    button.compact {{
      margin-top: 0;
      width: auto;
      padding: 0.35rem 0.7rem;
      font-size: 0.82rem;
      font-weight: 600;
      border-radius: 6px;
      white-space: nowrap;
    }}
    button.danger {{
      background: #b91c1c;
    }}
    a.compact {{
      display: inline-block;
      margin-top: 0;
      padding: 0.35rem 0.7rem;
      font-size: 0.82rem;
      font-weight: 600;
      border-radius: 6px;
      white-space: nowrap;
      text-decoration: none;
      color: #fff;
      background: #44403c;
      line-height: 1.2;
    }}
    button:hover, a.compact:hover {{ filter: brightness(0.95); }}
    textarea.mrz {{
      font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
      font-size: 0.85rem;
      letter-spacing: 0.02em;
      min-height: 4.5rem;
    }}
    .mrz-box {{
      background: var(--card);
      border: 1px dashed var(--line);
      border-radius: 12px;
      padding: 1rem;
      margin-bottom: 1rem;
    }}
    ul {{ padding-left: 1.1rem; }}
    ul.clean {{ list-style: none; padding: 0; margin: 0; }}
    li {{ margin: 0.45rem 0; }}
    .list-row {{
      display: flex;
      gap: 0.75rem;
      align-items: flex-start;
      justify-content: space-between;
      margin: 0;
      padding: 0.65rem 0;
      border-bottom: 1px solid var(--line);
    }}
    .list-row:last-child {{ border-bottom: 0; }}
    .list-row .meta {{ flex: 1; min-width: 0; word-break: break-word; }}
    .list-row .meta code {{
      display: inline-block;
      max-width: 100%;
      font-size: 0.78rem;
      word-break: break-all;
    }}
    .list-row .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      justify-content: flex-end;
      flex-shrink: 0;
    }}
    .list-row .actions form {{
      display: inline;
      margin: 0;
      padding: 0;
      background: none;
      border: 0;
      border-radius: 0;
    }}
    .tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin: 0.75rem 0 0.5rem;
    }}
    .tabs a {{
      display: inline-block;
      padding: 0.35rem 0.7rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      text-decoration: none;
      color: var(--ink);
      font-size: 0.85rem;
      font-weight: 600;
      background: #fff;
    }}
    .tabs a.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .status-pill {{
      display: inline-block;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
      vertical-align: middle;
    }}
    .status-ready {{ background: #ffedd5; color: #9a3412; }}
    .status-sent {{ background: #dbeafe; color: #1e40af; }}
    .status-filled {{ background: #d1fae5; color: #065f46; }}
    .date-group {{
      margin: 1rem 0 0.35rem;
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .list-row.compact {{
      padding: 0.45rem 0;
      align-items: center;
    }}
    .list-row.compact .meta {{
      font-size: 0.92rem;
    }}
    .list-row.compact .meta .subline {{
      font-size: 0.8rem;
      color: var(--muted);
      margin-top: 0.15rem;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
      margin: 0.5rem 0 0.75rem;
    }}
    .toolbar form {{
      display: flex;
      gap: 0.4rem;
      align-items: center;
      flex: 1;
      min-width: 12rem;
      margin: 0;
      padding: 0;
      background: none;
      border: 0;
    }}
    .toolbar input[type="search"] {{
      margin: 0;
      padding: 0.4rem 0.6rem;
    }}
    .toolbar button {{
      margin: 0;
      width: auto;
      padding: 0.4rem 0.75rem;
      font-size: 0.85rem;
    }}
    dl {{ margin: 0; }}
    dt {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.7rem; }}
    dd {{ margin: 0.15rem 0 0; font-weight: 600; }}
    .note {{
      margin-top: 1rem;
      font-size: 0.9rem;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main>
    <h1>{_esc(title)}</h1>
    {body}
  </main>
</body>
</html>
"""


def _apply_mrz(data: dict[str, str]) -> tuple[dict[str, str], str, str]:
    """Merge MRZ parse into form values. Returns (values, error, ok_message)."""
    values = {key: str(data.get(key) or "") for key, _req, _label in FIELD_SPEC}
    for key in (
        "nationality_other",
        "document_country_other",
        "residence_country_other",
        "checkin_date",
        "checkout_date",
        "email",
        "phone",
        "room",
        "notes",
    ):
        if key not in values:
            values[key] = str(data.get(key) or "")
    # Keep stay dates / contact if already typed
    for key in ("checkin_date", "checkout_date", "email", "phone", "room", "notes", "residence_place", "birth_place"):
        values[key] = str(data.get(key) or values.get(key) or "")
    try:
        result = parse_mrz(str(data.get("mrz") or ""))
    except MrzError as exc:
        return values, str(exc), ""
    for key, value in result.fields.items():
        if not value:
            continue
        if key in {"nationality", "document_country", "residence_country"}:
            code = value.upper()
            if code in _KNOWN_COUNTRIES:
                values[key] = code
                values[f"{key}_other"] = ""
            else:
                values[key] = "OTHER"
                values[f"{key}_other"] = code
        else:
            values[key] = value
    warn = (" Warnings: " + "; ".join(result.warnings)) if result.warnings else ""
    return values, "", f"MRZ applied — check the fields, then set check-in/out dates.{warn}"


def _fields_for_form(fields: dict[str, str]) -> dict[str, str]:
    """Expand stored ICAO codes into form select + Other fields."""
    values = {str(k): str(v or "") for k, v in fields.items()}
    for key in ("nationality", "document_country", "residence_country"):
        code = str(values.get(key) or "").upper()
        if code and code not in _KNOWN_COUNTRIES:
            values[key] = "OTHER"
            values[f"{key}_other"] = code
        elif code in _KNOWN_COUNTRIES:
            values[f"{key}_other"] = values.get(f"{key}_other") or ""
    return values


def _form_page(
    values: dict[str, str],
    error: str = "",
    *,
    ok_message: str = "",
    mrz_text: str = "",
    edit_id: str = "",
    record: dict | None = None,
) -> str:
    def v(name: str) -> str:
        return _esc(values.get(name) or "")

    editing = bool(edit_id)
    mrz_action = f"/checkin/{_esc(edit_id)}/edit/mrz" if editing else "/checkin/mrz"
    form_action = f"/checkin/{_esc(edit_id)}/edit" if editing else "/checkin"
    submit_label = "Save changes" if editing else "Submit check-in"
    title = "Edit check-in" if editing else "Check-in — Whatelse Guest House"
    sub = (
        "Update guest details. Saving changes the local record only."
        if editing
        else "Whatelse Guest House — staff check-in. Paste MRZ or type fields, then save."
    )
    siba_note = ""
    if editing and record and record.get("siba_sent_at"):
        siba_note = (
            '<div class="error">SIBA was already sent for this guest. '
            "Editing updates the hotel list only — it does not change AIMA. "
            "Use Send to SIBA again only if a new boletim is required.</div>"
        )
    cancel = (
        f'<p class="note"><a href="/checkin/{_esc(edit_id)}">Cancel</a> · <a href="/list">Hotel list</a></p>'
        if editing
        else ""
    )
    err = f'<div class="error">{_esc(error)}</div>' if error else ""
    ok = f'<div class="ok">{_esc(ok_message)}</div>' if ok_message else ""
    body = f"""
    <p class="sub">{_esc(sub)}</p>
    {siba_note}
    {err}
    {ok}
    <div class="mrz-box">
      <form method="post" action="{mrz_action}">
        <label for="mrz">Passport / ID MRZ <span class="opt">optional shortcut</span></label>
        <textarea id="mrz" name="mrz" class="mrz" maxlength="200" placeholder="Paste the 2 lines from the bottom of the passport (or 3 lines from an ID card)">{_esc(mrz_text)}</textarea>
        <p class="hint">Does not save yet — only fills the form. Check-in/out dates stay manual.</p>
        <input type="hidden" name="checkin_date" value="{v("checkin_date")}" />
        <input type="hidden" name="checkout_date" value="{v("checkout_date")}" />
        <input type="hidden" name="email" value="{v("email")}" />
        <input type="hidden" name="phone" value="{v("phone")}" />
        <input type="hidden" name="room" value="{v("room")}" />
        <input type="hidden" name="residence_place" value="{v("residence_place")}" />
        <input type="hidden" name="birth_place" value="{v("birth_place")}" />
        <button class="secondary inline" type="submit">Fill form from MRZ</button>
      </form>
    </div>
    <form method="post" action="{form_action}" novalidate>
      <div class="row two">
        <div>
          <label for="surname">Surname (Apelido)</label>
          <input id="surname" name="surname" required maxlength="40" value="{v("surname")}" autocomplete="family-name" />
        </div>
        <div>
          <label for="given_name">Given name (Nome)</label>
          <input id="given_name" name="given_name" required maxlength="40" value="{v("given_name")}" autocomplete="given-name" />
        </div>
      </div>

      <div class="row two">
        <div>
          <label for="nationality">Nationality</label>
          <select id="nationality" name="nationality" required>
            {_country_options(values.get("nationality") or "")}
          </select>
          <p class="hint">If Other, type the 3-letter code below (e.g. PRT).</p>
          <input name="nationality_other" maxlength="3" placeholder="Other code" value="{v("nationality_other")}" />
        </div>
        <div>
          <label for="birth_date">Date of birth</label>
          <input id="birth_date" name="birth_date" type="date" required value="{v("birth_date")}" />
        </div>
      </div>

      <label for="birth_place">Place of birth <span class="opt">optional</span></label>
      <input id="birth_place" name="birth_place" maxlength="30" value="{v("birth_place")}" />

      <div class="row two">
        <div>
          <label for="document_type">Document type</label>
          <select id="document_type" name="document_type" required>
            {_doc_options(values.get("document_type") or "")}
          </select>
        </div>
        <div>
          <label for="document_number">Document number</label>
          <input id="document_number" name="document_number" required maxlength="16" value="{v("document_number")}" />
        </div>
      </div>

      <div class="row two">
        <div>
          <label for="document_country">Document issuing country</label>
          <select id="document_country" name="document_country" required>
            {_country_options(values.get("document_country") or "")}
          </select>
          <input name="document_country_other" maxlength="3" placeholder="Other code" value="{v("document_country_other")}" />
        </div>
        <div>
          <label for="residence_country">Country of residence</label>
          <select id="residence_country" name="residence_country" required>
            {_country_options(values.get("residence_country") or "")}
          </select>
          <input name="residence_country_other" maxlength="3" placeholder="Other code" value="{v("residence_country_other")}" />
        </div>
      </div>

      <label for="residence_place">Place of residence <span class="opt">optional</span></label>
      <input id="residence_place" name="residence_place" maxlength="30" value="{v("residence_place")}" />

      <div class="row two">
        <div>
          <label for="checkin_date">Check-in date</label>
          <input id="checkin_date" name="checkin_date" type="date" required value="{v("checkin_date")}" />
        </div>
        <div>
          <label for="checkout_date">Check-out date</label>
          <input id="checkout_date" name="checkout_date" type="date" required value="{v("checkout_date")}" />
        </div>
      </div>

      <div class="row two">
        <div>
          <label for="email">Email <span class="opt">optional</span></label>
          <input id="email" name="email" type="email" value="{v("email")}" autocomplete="email" />
        </div>
        <div>
          <label for="phone">Phone <span class="opt">optional</span></label>
          <input id="phone" name="phone" value="{v("phone")}" autocomplete="tel" />
        </div>
      </div>

      <label for="room">Room <span class="opt">optional</span></label>
      <input id="room" name="room" maxlength="20" value="{v("room")}" />

      <label for="notes">Notes for the hotel <span class="opt">optional</span></label>
      <textarea id="notes" name="notes" maxlength="500">{v("notes")}</textarea>

      <button type="submit">{_esc(submit_label)}</button>
      {cancel}
      <p class="note">Saved on this PC. Send foreign guests to SIBA from the list; guest links at <a href="/links">/links</a>; INE at <a href="/ine">/ine</a>.
      Hotel list: <a href="/list">/list</a></p>
    </form>
    """
    return _shell(title, body)


def _thanks_page(record: dict) -> str:
    data = dict(record.get("fields") or {})
    rows: list[str] = []
    for name, _req, label in FIELD_SPEC:
        value = data.get(name) or ""
        if not value:
            continue
        rows.append(f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>")
    checkin_id = str(record.get("id") or "")
    body = f"""
    <p class="sub">Thank you. Your check-in was saved.</p>
    <div class="panel">
      <dl>
        <dt>Reference</dt><dd>{_esc(checkin_id)}</dd>
        {"".join(rows)}
      </dl>
      <p class="note">Saved locally. Hotel staff can send foreign guests to SIBA from the list.</p>
      <p class="note"><a href="/checkin">Submit another guest</a> · <a href="/list">Hotel list</a> · <a href="/ine">INE month</a></p>
    </div>
    """
    return _shell("Check-in received", body)


def _list_page(rows: list[dict], *, message: str = "", ok: bool = True) -> str:
    banner = ""
    if message:
        banner = f'<div class="{"ok" if ok else "error"}">{_esc(message)}</div>'
    if not rows:
        items = "<p class=\"note\">No check-ins saved yet.</p>"
    else:
        parts: list[str] = ['<ul class="clean">']
        for row in rows:
            fields = dict(row.get("fields") or {})
            name = " ".join(
                part for part in (fields.get("given_name") or "", fields.get("surname") or "") if part
            ).strip() or "(no name)"
            when = str(row.get("created_at") or "")
            checkin = str(fields.get("checkin_date") or "")
            cid = str(row.get("id") or "")
            siba = "SIBA sent" if row.get("siba_sent_at") else (
                "SIBA pending" if is_foreign_guest(fields) else "PRT — no SIBA"
            )
            parts.append(
                '<li class="list-row">'
                '<div class="meta">'
                f'<a href="/checkin/{_esc(cid)}">{_esc(name)}</a>'
                f" — check-in {_esc(checkin)} — {_esc(siba)} — saved {_esc(when)}"
                "</div>"
                '<div class="actions">'
                f'<a class="compact" href="/checkin/{_esc(cid)}/edit">Edit</a>'
                f'<form method="post" action="/checkin/{_esc(cid)}/remove" '
                f'onsubmit="return confirm(\'Remove this check-in from the hotel list?\');">'
                '<button class="danger compact" type="submit">Remove</button>'
                "</form>"
                "</div>"
                "</li>"
            )
        parts.append("</ul>")
        items = "".join(parts)
    body = f"""
    <p class="sub">Hotel list ({store.count_checkins()}). Foreign guests can be sent to SIBA from each detail page.</p>
    {banner}
    <div class="panel">
      {items}
      <p class="note"><a href="/checkin">New check-in</a> · <a href="/links">Guest links</a> · <a href="/ine">INE monthly report</a></p>
    </div>
    """
    return _shell("Check-in list", body)


def _detail_page(record: dict, *, message: str = "", ok: bool = True) -> str:
    data = dict(record.get("fields") or {})
    rows: list[str] = [
        f"<dt>Id</dt><dd>{_esc(str(record.get('id') or ''))}</dd>",
        f"<dt>Saved at</dt><dd>{_esc(str(record.get('created_at') or ''))}</dd>",
        f"<dt>SIBA sent</dt><dd>{_esc(str(record.get('siba_sent_at') or 'not yet'))}</dd>",
    ]
    if record.get("siba_file_number"):
        rows.append(
            f"<dt>SIBA file number</dt><dd>{_esc(str(record.get('siba_file_number')))}</dd>"
        )
    for name, _req, label in FIELD_SPEC:
        value = data.get(name) or ""
        if not value:
            continue
        rows.append(f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>")
    banner = ""
    if message:
        banner = f'<div class="{"ok" if ok else "error"}">{_esc(message)}</div>'
    cid = str(record.get("id") or "")
    body = f"""
    <p class="sub">Saved check-in detail (hotel).</p>
    {banner}
    <div class="panel">
      <dl>{"".join(rows)}</dl>
      {_siba_panel(record)}
      <div class="actions" style="margin-top:1rem;display:flex;flex-wrap:wrap;gap:0.35rem;">
        <a class="compact" href="/checkin/{_esc(cid)}/edit">Edit</a>
        <form method="post" action="/checkin/{_esc(cid)}/remove"
          onsubmit="return confirm('Remove this check-in from the hotel list?');"
          style="display:inline;margin:0;padding:0;background:none;border:0;">
          <button class="danger compact" type="submit">Remove</button>
        </form>
      </div>
      <p class="note"><a href="/list">Back to list</a> · <a href="/checkin">New check-in</a> · <a href="/ine">INE month</a></p>
    </div>
    """
    return _shell("Check-in detail", body)


def _ine_month_from_query(query: str) -> tuple[int, int, str, bool]:
    params = parse_qs(query, keep_blank_values=True)
    raw = (params.get("month") or [""])[-1]
    parsed = parse_year_month(raw) if raw else None
    if parsed is None and raw:
        y, m = default_report_month()
        return y, m, "Use month as YYYY-MM.", False
    if parsed is None:
        return (*default_report_month(), "", True)
    return (*parsed, "", True)


def _ine_page(report: MonthReport, *, message: str = "", ok: bool = True) -> str:
    cfg = ine_settings()
    ready = ine_ready()
    webinq = cfg["url"].rstrip("/") or "https://webinq.ine.pt"
    if ready:
        login_hint = f"WebInq aderente in .env: {_esc(cfg['user'])} (password stays in .env)."
    else:
        login_hint = _esc(ready)

    banner = ""
    if message:
        banner = f'<div class="{"ok" if ok else "error"}">{_esc(message)}</div>'

    if report.no_movement:
        table = (
            '<p class="note">No guest nights in this month from saved check-ins. '
            "In WebInq, mark <strong>sem movimento de hóspedes</strong> if that is correct "
            "(still submit by day 10).</p>"
        )
    else:
        body_rows = []
        for row in report.rows:
            entered = "" if row.entered == 0 else str(row.entered)
            body_rows.append(
                "<tr>"
                f"<td>{_esc(row.label)} <span class=\"hint\">({_esc(row.code)})</span></td>"
                f"<td class=\"num\">{_esc(entered)}</td>"
                f"<td class=\"num\">{row.slept}</td>"
                f"<td class=\"num\">{row.dormidas}</td>"
                "</tr>"
            )
        table = f"""
        <table>
          <thead>
            <tr>
              <th>País de residência</th>
              <th class="num">Entraram</th>
              <th class="num">Dormiram</th>
              <th class="num">Dormidas</th>
            </tr>
          </thead>
          <tbody>
            {"".join(body_rows)}
          </tbody>
          <tfoot>
            <tr>
              <td>Total</td>
              <td class="num">{report.total_entered}</td>
              <td class="num">{report.total_slept}</td>
              <td class="num">{report.total_dormidas}</td>
            </tr>
          </tfoot>
        </table>
        """

    skip = ""
    if report.skipped:
        skip = (
            '<p class="note">Skipped incomplete rows: '
            + _esc("; ".join(report.skipped[:8]))
            + ("…" if len(report.skipped) > 8 else "")
            + "</p>"
        )

    month_value = report.label
    body = f"""
    <p class="sub">IPHH Quadro 1 from local check-ins. Copy into WebInq (deadline: day 10 of the next month). Not auto-sent.</p>
    {banner}
    <div class="panel">
      <form method="get" action="/ine">
        <label for="month">Reference month</label>
        <input id="month" name="month" type="month" value="{_esc(month_value)}" required />
        <button type="submit">Show report</button>
      </form>
      <p class="hint">{login_hint}</p>
      <p class="note"><a href="{_esc(webinq)}" target="_blank" rel="noopener">Open WebInq</a>
        · <a href="/ine.csv?month={_esc(month_value)}">Download CSV</a></p>
      {table}
      {skip}
      <p class="note">Quadro 2 (capacity / revenue) is not in this app — fill in WebInq from eviivo / accounts.</p>
      <form method="post" action="/ine/mark">
        <input type="hidden" name="month" value="{_esc(month_value)}" />
        <button class="secondary" type="submit">Mark these guests as used for INE {_esc(month_value)}</button>
        <p class="hint">Optional hotel reminder only — does not submit to INE.</p>
      </form>
      <p class="note"><a href="/list">Hotel list</a> · <a href="/checkin">New check-in</a></p>
    </div>
    """
    return _shell(f"INE IPHH — {month_value}", body)


def _siba_panel(record: dict) -> str:
    fields = dict(record.get("fields") or {})
    cid = str(record.get("id") or "")
    if record.get("siba_sent_at"):
        return f'<p class="note">Already sent to SIBA at {_esc(str(record.get("siba_sent_at")))}.</p>'
    if not is_foreign_guest(fields):
        return '<p class="note">Portuguese nationality (PRT) — SIBA send is not used.</p>'
    return f"""
    <form method="post" action="/checkin/{_esc(cid)}/send-siba">
      <button class="secondary" type="submit">Send to SIBA / AIMA</button>
      <p class="note">Sends this foreign guest boletim now. Needs SIBA values in .env.</p>
    </form>
    """


def _send_siba(checkin_id: str) -> tuple[dict | None, str, bool]:
    record = store.get_checkin(checkin_id)
    if record is None:
        return None, "Check-in not found.", False
    if record.get("siba_sent_at"):
        return record, "Already sent to SIBA.", True
    fields = dict(record.get("fields") or {})
    try:
        file_number = store.next_siba_file_number()
        result = send_checkin(fields, file_number=file_number)
        store.mark_siba_sent(checkin_id, file_number=file_number, result=result)
        updated = store.get_checkin(checkin_id) or record
        print(f"SIBA ok for {checkin_id} file {file_number}.", flush=True)
        return updated, "Sent to SIBA / AIMA successfully.", True
    except SibaError as exc:
        print(f"SIBA fail for {checkin_id}: {exc}", flush=True)
        return record, str(exc), False


def _save_and_maybe_siba(
    fields: dict[str, str],
    *,
    source: str,
    link_token: str = "",
    booking_reference: str = "",
    auto_siba: bool = True,
) -> tuple[dict, str]:
    record = store.save_checkin(
        fields,
        source=source,
        link_token=link_token,
        booking_reference=booking_reference,
    )
    if not auto_siba:
        return record, ""
    if not is_foreign_guest(fields):
        return record, "prt_skip"
    try:
        file_number = store.next_siba_file_number()
        result = send_checkin(fields, file_number=file_number)
        store.mark_siba_sent(str(record["id"]), file_number=file_number, result=result)
        return store.get_checkin(str(record["id"])) or record, "ok"
    except SibaError as exc:
        print(f"SIBA auto-fail {record['id']}: {exc}", flush=True)
        return record, f"fail:{exc}"


def _guest_form_page(
    token: str,
    link: dict,
    values: dict[str, str],
    *,
    error: str = "",
    ok_message: str = "",
    mrz_text: str = "",
) -> str:
    def v(name: str) -> str:
        return _esc(values.get(name) or "")

    err = f'<div class="error">{_esc(error)}</div>' if error else ""
    ok = f'<div class="ok">{_esc(ok_message)}</div>' if ok_message else ""
    guest = str(link.get("guest") or "Guest")
    body = f"""
    <p class="sub">Online check-in for {_esc(str(link.get("property_name") or "Whatelse Guest House"))}.
    Stay: {_esc(str(link.get("checkin") or ""))} → {_esc(str(link.get("checkout") or ""))}
    {" · " + _esc(guest) if guest else ""}</p>
    {err}
    {ok}
    <div class="mrz-box">
      <form method="post" action="/g/{_esc(token)}/mrz">
        <label for="mrz">Passport / ID MRZ <span class="opt">optional shortcut</span></label>
        <textarea id="mrz" name="mrz" class="mrz" maxlength="200" placeholder="Paste the 2 lines from the bottom of the passport">{_esc(mrz_text)}</textarea>
        <button class="secondary inline" type="submit">Fill form from MRZ</button>
      </form>
    </div>
    <form method="post" action="/g/{_esc(token)}" novalidate>
      <input type="hidden" name="checkin_date" value="{_esc(str(link.get("checkin") or ""))}" />
      <input type="hidden" name="checkout_date" value="{_esc(str(link.get("checkout") or ""))}" />
      <div class="row two">
        <div>
          <label for="surname">Surname (Apelido)</label>
          <input id="surname" name="surname" required maxlength="40" value="{v("surname")}" />
        </div>
        <div>
          <label for="given_name">Given name (Nome)</label>
          <input id="given_name" name="given_name" required maxlength="40" value="{v("given_name")}" />
        </div>
      </div>
      <div class="row two">
        <div>
          <label for="nationality">Nationality</label>
          <select id="nationality" name="nationality" required>{_country_options(values.get("nationality") or "")}</select>
          <input name="nationality_other" maxlength="3" placeholder="Other code" value="{v("nationality_other")}" />
        </div>
        <div>
          <label for="birth_date">Date of birth</label>
          <input id="birth_date" name="birth_date" type="date" required value="{v("birth_date")}" />
        </div>
      </div>
      <label for="birth_place">Place of birth <span class="opt">optional</span></label>
      <input id="birth_place" name="birth_place" maxlength="30" value="{v("birth_place")}" />
      <div class="row two">
        <div>
          <label for="document_type">Document type</label>
          <select id="document_type" name="document_type" required>{_doc_options(values.get("document_type") or "")}</select>
        </div>
        <div>
          <label for="document_number">Document number</label>
          <input id="document_number" name="document_number" required maxlength="16" value="{v("document_number")}" />
        </div>
      </div>
      <div class="row two">
        <div>
          <label for="document_country">Document issuing country</label>
          <select id="document_country" name="document_country" required>{_country_options(values.get("document_country") or "")}</select>
          <input name="document_country_other" maxlength="3" placeholder="Other code" value="{v("document_country_other")}" />
        </div>
        <div>
          <label for="residence_country">Country of residence</label>
          <select id="residence_country" name="residence_country" required>{_country_options(values.get("residence_country") or "")}</select>
          <input name="residence_country_other" maxlength="3" placeholder="Other code" value="{v("residence_country_other")}" />
        </div>
      </div>
      <label for="residence_place">Place of residence <span class="opt">optional</span></label>
      <input id="residence_place" name="residence_place" maxlength="30" value="{v("residence_place")}" />
      <div class="row two">
        <div>
          <label for="email">Email <span class="opt">optional</span></label>
          <input id="email" name="email" type="email" value="{v("email")}" />
        </div>
        <div>
          <label for="phone">Phone <span class="opt">optional</span></label>
          <input id="phone" name="phone" value="{v("phone")}" />
        </div>
      </div>
      <button type="submit">Submit check-in</button>
      <p class="note">Foreign guests are sent to SIBA / AIMA automatically after submit.</p>
    </form>
    """
    return _shell("Online check-in — Whatelse Guest House", body)


def _guest_done_page(link: dict, *, already: bool, siba_note: str = "") -> str:
    if already:
        msg = "This check-in link was already completed. Thank you."
    else:
        msg = "Thank you — your check-in was saved."
        if siba_note == "ok":
            msg += " Registration was sent to the Portuguese authorities (SIBA/AIMA)."
        elif siba_note == "prt_skip":
            msg += " Portuguese nationality — SIBA send is not required."
        elif siba_note.startswith("fail:"):
            msg += " The hotel will finish the authority registration if needed."
    body = f"""
    <div class="panel">
      <p class="ok">{_esc(msg)}</p>
      <p class="note">You can close this page.</p>
    </div>
    """
    return _shell("Check-in received", body)


def _links_page(
    *,
    message: str = "",
    ok: bool = True,
    highlight: str = "",
    filter_key: str = "all",
    search: str = "",
) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    cfg = link_settings()
    days = int(cfg["days_before"])
    pub = link_public_ready()
    mail = link_mail_ready()
    today = datetime.now(ZoneInfo("Europe/Lisbon")).date().isoformat()
    filt = filter_key if filter_key in {"all", "ready", "sent", "filled"} else "all"
    q = (search or "").strip().lower()

    banner = ""
    if message:
        banner = f'<div class="{"ok" if ok else "error"}">{_esc(message)}</div>'
    hints = f'<p class="hint">Guest link target: <strong>{days} days</strong> before check-in (Joao). Auto job emails stays from today through that window (covers late bookings).</p>'
    if pub:
        hints += f'<p class="hint">{_esc(pub)}</p>'
    else:
        hints += f'<p class="hint">Public base: {_esc(str(cfg["public_base_url"]))}</p>'
    if mail:
        hints += f'<p class="hint">{_esc(mail)}</p>'
    evi = eviivo_ready()
    if evi:
        hints += f'<p class="hint">{_esc(evi)}</p>'
    else:
        hints += '<p class="hint">eviivo: ready for automatic guest-link emails.</p>'
    if not cron_secret():
        hints += '<p class="hint">Set CRON_SECRET in .env so Railway/cron can call /cron/send-due-links.</p>'

    last = links.last_auto_run()
    if last:
        summary = (
            f"Last auto run {_esc(str(last.get('at') or ''))}: "
            f"{len(last.get('sent') or [])} emailed, "
            f"{len(last.get('skipped') or [])} skipped, "
            f"{len(last.get('no_email') or [])} no email, "
            f"{len(last.get('failed') or [])} failed."
        )
        if last.get("error"):
            summary += f" Error: {_esc(str(last.get('error')))}"
        hints += f'<p class="hint">{summary}</p>'

    all_rows = links.list_links(500)
    counts = {"all": 0, "ready": 0, "sent": 0, "filled": 0}
    active_rows: list[dict] = []
    for row in all_rows:
        checkout = str(row.get("checkout") or "")
        if checkout and checkout < today:
            continue
        status = links.link_status(row)
        counts["all"] += 1
        counts[status] = counts.get(status, 0) + 1
        active_rows.append(row)

    filtered: list[dict] = []
    for row in active_rows:
        status = links.link_status(row)
        if filt != "all" and status != filt:
            continue
        if q:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("guest", "email", "reference", "checkin", "checkout")
            ).lower()
            if q not in blob:
                continue
        filtered.append(row)

    def list_query(*, filter: str | None = None, q_text: str | None = None) -> str:
        use_filter = filter if filter is not None else filt
        use_q = search if q_text is None else q_text
        parts = [f"filter={use_filter}"]
        if use_q:
            parts.append(f"q={quote(str(use_q))}")
        return "/links?" + "&".join(parts)

    tabs = []
    for key, label in (
        ("all", "All"),
        ("ready", "Ready"),
        ("sent", "Sent"),
        ("filled", "Filled"),
    ):
        cls = "active" if filt == key else ""
        tabs.append(
            f'<a class="{cls}" href="{list_query(filter=key)}">{label} ({counts.get(key, 0)})</a>'
        )

    groups: dict[str, list[dict]] = {}
    for row in filtered:
        checkin = str(row.get("checkin") or "") or "(no date)"
        groups.setdefault(checkin, []).append(row)

    blocks: list[str] = []
    for checkin in sorted(groups.keys()):
        label = f"Arriving {checkin}" if checkin != "(no date)" else "No check-in date"
        blocks.append(f'<div class="date-group">{_esc(label)} · {len(groups[checkin])}</div>')
        blocks.append('<ul class="clean">')
        for row in groups[checkin]:
            token = str(row.get("token") or "")
            status = links.link_status(row)
            mark = " <strong>(new)</strong>" if token == highlight else ""
            guest = str(row.get("guest") or "(guest)")
            checkout = str(row.get("checkout") or "")
            email = str(row.get("email") or "")
            ref = str(row.get("reference") or "")
            actions = []
            if status != "filled" and email:
                actions.append(
                    f'<form method="post" action="/links/email">'
                    f'<input type="hidden" name="token" value="{_esc(token)}" />'
                    f'<button class="secondary compact" type="submit">Email</button>'
                    f"</form>"
                )
            actions.append(
                f'<form method="post" action="/links/remove" '
                f'onsubmit="return confirm(\'Remove this guest link?\');">'
                f'<input type="hidden" name="token" value="{_esc(token)}" />'
                f'<button class="danger compact" type="submit">Remove</button>'
                f"</form>"
            )
            sub_bits = [f"{checkin} → {checkout}" if checkout else checkin]
            if email:
                sub_bits.append(email)
            if ref:
                sub_bits.append(ref)
            blocks.append(
                '<li class="list-row compact">'
                '<div class="meta">'
                f'<span class="status-pill status-{status}">{status}</span> '
                f"<strong>{_esc(guest)}</strong>{mark}"
                f'<div class="subline">{_esc(" · ".join(sub_bits))}</div>'
                "</div>"
                f'<div class="actions">{"".join(actions)}</div>'
                "</li>"
            )
        blocks.append("</ul>")

    if filtered:
        items = "".join(blocks)
    elif all_rows and not active_rows:
        items = '<p class="note">No current links (past check-outs are hidden).</p>'
    elif all_rows:
        items = '<p class="note">No links match this filter.</p>'
    else:
        items = '<p class="note">No guest links yet.</p>'

    body = f"""
    <p class="sub">Create a guest check-in link (like CheckIn Scan). Guest fills online; foreign guests auto-send to SIBA.</p>
    {banner}
    <div class="panel">
      {hints}
      <form method="post" action="/links/create">
        <label for="guest">Guest name</label>
        <input id="guest" name="guest" maxlength="80" />
        <label for="email">Guest email</label>
        <input id="email" name="email" type="email" />
        <label for="reference">Booking reference <span class="opt">optional</span></label>
        <input id="reference" name="reference" maxlength="80" placeholder="eviivo / Booking ref" />
        <div class="row two">
          <div>
            <label for="checkin">Check-in</label>
            <input id="checkin" name="checkin" type="date" required />
          </div>
          <div>
            <label for="checkout">Check-out</label>
            <input id="checkout" name="checkout" type="date" required />
          </div>
        </div>
        <button type="submit">Create guest link</button>
      </form>
      <form method="post" action="/links/auto-run" style="margin-top:1rem;"
        onsubmit="return confirm('Run automatic eviivo emails for stays within the next {_esc(str(days))} days?');">
        <button class="secondary inline" type="submit">Run auto emails now</button>
        <p class="hint">Pulls Whatelse bookings from eviivo and emails links not yet sent. Same job as the daily cron.</p>
      </form>
      <h2 style="font-size:1.05rem;margin:1.4rem 0 0.35rem">Links</h2>
      <div class="tabs">{"".join(tabs)}</div>
      <div class="toolbar">
        <form method="get" action="/links">
          <input type="hidden" name="filter" value="{_esc(filt)}" />
          <input type="search" name="q" value="{_esc(search)}" placeholder="Search name, email, ref" />
          <button class="secondary" type="submit">Search</button>
        </form>
        <span class="hint" style="margin:0">showing {len(filtered)}</span>
      </div>
      {items}
      <p class="note"><a href="/list">Hotel list</a> · <a href="/checkin">Staff check-in</a> · <a href="/ine">INE</a></p>
    </div>
    """
    return _shell("Guest check-in links", body)
