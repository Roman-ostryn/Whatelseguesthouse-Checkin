"""Parse passport / ID MRZ (ICAO 9303) into check-in form fields.

Hotel workflow: paste the 2 (passport) or 3 (ID card) MRZ lines from the
document bottom. Camera OCR can be added later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

LISBON = ZoneInfo("Europe/Lisbon")
_WEIGHTS = (7, 3, 1)


class MrzError(ValueError):
    pass


@dataclass(frozen=True)
class MrzResult:
    fields: dict[str, str]
    warnings: list[str]


def parse_mrz(text: str) -> MrzResult:
    """Return form field values from raw MRZ paste."""
    lines = _normalize_lines(text)
    if len(lines) >= 3 and all(len(x) == 30 for x in lines[:3]):
        return _parse_td1(lines[0], lines[1], lines[2])
    if len(lines) >= 2 and all(len(x) == 36 for x in lines[:2]):
        return _parse_td2(lines[0], lines[1])
    if len(lines) >= 2 and all(len(x) == 44 for x in lines[:2]):
        return _parse_td3(lines[0], lines[1])
    if not lines:
        raise MrzError("Paste the MRZ lines from the bottom of the passport/ID.")
    lengths = ", ".join(str(len(x)) for x in lines[:3])
    raise MrzError(
        "Unrecognized MRZ. Need passport 2×44 chars, or ID 3×30 / 2×36. "
        f"Got line lengths: {lengths}."
    )


def _normalize_lines(text: str) -> list[str]:
    raw = str(text or "").upper().replace(" ", "")
    # Common OCR confusions
    raw = raw.replace("«", "<").replace("‹", "<")
    parts = re.split(r"[\r\n]+", raw)
    lines: list[str] = []
    for part in parts:
        line = re.sub(r"[^A-Z0-9<]", "", part)
        if line:
            lines.append(line)
    # Sometimes pasted as one long string
    if len(lines) == 1:
        blob = lines[0]
        if len(blob) == 88:
            return [blob[:44], blob[44:]]
        if len(blob) == 90:
            return [blob[:30], blob[30:60], blob[60:]]
        if len(blob) == 72:
            return [blob[:36], blob[36:]]
    return lines


def _char_value(ch: str) -> int:
    if ch == "<":
        return 0
    if "0" <= ch <= "9":
        return ord(ch) - ord("0")
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    raise MrzError(f"Invalid MRZ character: {ch!r}")


def _check_digit(data: str) -> str:
    total = 0
    for i, ch in enumerate(data):
        total += _char_value(ch) * _WEIGHTS[i % 3]
    return str(total % 10)


def _verify(data: str, digit: str, label: str, warnings: list[str], *, strict: bool = False) -> None:
    if digit == "<":
        return
    expected = _check_digit(data)
    if digit != expected:
        msg = f"{label} check digit mismatch (got {digit}, expected {expected})."
        if strict:
            raise MrzError(msg)
        warnings.append(msg)


def _parse_names(name_field: str) -> tuple[str, str]:
    parts = name_field.rstrip("<").split("<<", 1)
    surname = parts[0].replace("<", " ").strip()
    given = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""
    surname = re.sub(r"\s+", " ", surname)
    given = re.sub(r"\s+", " ", given)
    # Form requires a given name; SIBA allows a placeholder when the document has one name only.
    if surname and not given:
        given = "-"
    return surname, given


def _parse_date_yyymmdd(raw: str, warnings: list[str], *, label: str) -> str:
    if not re.fullmatch(r"\d{6}", raw):
        warnings.append(f"{label} date is not numeric.")
        return ""
    yy, mm, dd = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    if mm < 1 or mm > 12 or dd < 1 or dd > 31:
        warnings.append(f"{label} date looks invalid ({raw}).")
        return ""
    today = datetime.now(LISBON).date()
    # Birth: prefer century that makes age 0..120
    if label == "Birth":
        candidates = [date(1900 + yy, mm, dd), date(2000 + yy, mm, dd)]
        ok = [d for d in candidates if d <= today and (today.year - d.year) <= 120]
        if not ok:
            # try anyway with nearer past
            ok = [d for d in candidates if d <= today] or candidates
        chosen = max(ok)
        return chosen.isoformat()
    # Expiry / other: prefer future, else nearer
    c1900 = date(1900 + yy, mm, dd)
    c2000 = date(2000 + yy, mm, dd)
    future = [d for d in (c1900, c2000) if d >= today]
    chosen = min(future) if future else max(c1900, c2000)
    return chosen.isoformat()


def _doc_type(code: str) -> str:
    code = (code or "P").upper()
    if code.startswith("P"):
        return "P"
    if code.startswith("I") or code.startswith("A") or code.startswith("C"):
        return "B"
    return "O"


def _parse_td3(line1: str, line2: str) -> MrzResult:
    warnings: list[str] = []
    if not line1.startswith("P"):
        warnings.append("Line 1 does not start with P (passport); parsing anyway.")
    issuer = line1[2:5]
    surname, given = _parse_names(line1[5:44])
    doc_num = line2[0:9].replace("<", "")
    _verify(line2[0:9], line2[9], "Document number", warnings)
    nationality = line2[10:13]
    birth = _parse_date_yyymmdd(line2[13:19], warnings, label="Birth")
    _verify(line2[13:19], line2[19], "Birth date", warnings)
    expiry = _parse_date_yyymmdd(line2[21:27], warnings, label="Expiry")
    _verify(line2[21:27], line2[27], "Expiry date", warnings)
    optional = line2[28:42]
    _verify(line2[0:10] + line2[13:20] + line2[21:43], line2[43], "Composite", warnings)

    fields: dict[str, str] = {
        "surname": surname,
        "given_name": given,
        "birth_date": birth,
        "document_number": doc_num,
        "document_type": _doc_type(line1[0:2]),
    }
    # Issuing state vs nationality can differ
    nat = nationality.replace("<", "")
    iss = issuer.replace("<", "")
    if nat and len(nat) == 3 and nat != "UTO":
        fields["nationality"] = nat
        fields["residence_country"] = nat
    if iss and len(iss) == 3 and iss != "UTO":
        fields["document_country"] = iss
    elif nat and len(nat) == 3 and nat != "UTO":
        fields["document_country"] = nat
    if expiry:
        warnings.append(f"Document expiry in MRZ: {expiry}")
    if optional.replace("<", ""):
        warnings.append("Optional MRZ data present (ignored).")
    return MrzResult(fields=fields, warnings=warnings)


def _parse_td1(line1: str, line2: str, line3: str) -> MrzResult:
    """ID-1 cards (credit-card size), 3×30."""
    warnings: list[str] = []
    doc_num = (line1[5:14] + line1[15:30]).replace("<", "")[:16]
    _verify(line1[5:14], line1[14], "Document number", warnings)
    birth = _parse_date_yyymmdd(line2[0:6], warnings, label="Birth")
    _verify(line2[0:6], line2[6], "Birth date", warnings)
    nationality = line2[15:18]
    issuer = line1[2:5]
    surname, given = _parse_names(line3[0:30])
    fields: dict[str, str] = {
        "surname": surname,
        "given_name": given,
        "birth_date": birth,
        "document_number": doc_num,
        "document_type": _doc_type(line1[0:2]),
    }
    nat = nationality.replace("<", "")
    iss = issuer.replace("<", "")
    if nat and len(nat) == 3:
        fields["nationality"] = nat
        fields["residence_country"] = nat
    if iss and len(iss) == 3:
        fields["document_country"] = iss
    elif nat and len(nat) == 3:
        fields["document_country"] = nat
    return MrzResult(fields=fields, warnings=warnings)


def _parse_td2(line1: str, line2: str) -> MrzResult:
    """ID-2 format, 2×36 (less common)."""
    warnings: list[str] = []
    issuer = line1[2:5]
    surname, given = _parse_names(line1[5:36])
    doc_num = line2[0:9].replace("<", "")
    _verify(line2[0:9], line2[9], "Document number", warnings)
    nationality = line2[10:13]
    birth = _parse_date_yyymmdd(line2[13:19], warnings, label="Birth")
    _verify(line2[13:19], line2[19], "Birth date", warnings)
    fields: dict[str, str] = {
        "surname": surname,
        "given_name": given,
        "birth_date": birth,
        "document_number": doc_num,
        "document_type": _doc_type(line1[0:2]),
    }
    nat = nationality.replace("<", "")
    iss = issuer.replace("<", "")
    if nat and len(nat) == 3:
        fields["nationality"] = nat
        fields["residence_country"] = nat
    if iss and len(iss) == 3:
        fields["document_country"] = iss
    elif nat and len(nat) == 3:
        fields["document_country"] = nat
    return MrzResult(fields=fields, warnings=warnings)
