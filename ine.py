"""Monthly INE IPHH (WebInq) Quadro 1 summary from all Whatelse eviivo stays.

INE has no simple SIBA-like send API for IPHH. This builds the guest/dormidas
table Joao copies into https://webinq.ine.pt (due by day 10 of the next month).
IPHH uses country of *residence*, not nationality.
"""

from __future__ import annotations

import csv
import io
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fields import COMMON_COUNTRIES

LISBON = ZoneInfo("Europe/Lisbon")

# Portuguese labels as shown on the IPHH form (residence country).
_ICAO_PT: dict[str, str] = {
    "PRT": "Portugal (residentes)",
    "ZAF": "África do Sul",
    "DEU": "Alemanha",
    "AGO": "Angola",
    "ARG": "Argentina",
    "AUS": "Austrália",
    "AUT": "Áustria",
    "BEL": "Bélgica",
    "BRA": "Brasil",
    "BGR": "Bulgária",
    "CPV": "Cabo Verde",
    "CAN": "Canadá",
    "CZE": "Chéquia",
    "CHN": "China",
    "CYP": "Chipre",
    "KOR": "Coreia (República da)",
    "HRV": "Croácia",
    "DNK": "Dinamarca",
    "SVK": "Eslováquia",
    "SVN": "Eslovénia",
    "ESP": "Espanha",
    "USA": "Estados Unidos da América",
    "EST": "Estónia",
    "FIN": "Finlândia",
    "RUS": "Federação da Rússia",
    "FRA": "França",
    "GRC": "Grécia",
    "GNB": "Guiné-Bissau",
    "HUN": "Hungria",
    "IND": "Índia",
    "IRL": "Irlanda",
    "ISL": "Islândia",
    "ISR": "Israel",
    "ITA": "Itália",
    "JPN": "Japão",
    "LVA": "Letónia",
    "LIE": "Listenstaine",
    "LTU": "Lituânia",
    "LUX": "Luxemburgo",
    "MLT": "Malta",
    "MEX": "México",
    "NOR": "Noruega",
    "NZL": "Nova Zelândia",
    "NLD": "Países Baixos",
    "POL": "Polónia",
    "GBR": "Reino Unido",
    "ROU": "Roménia",
    "SWE": "Suécia",
    "CHE": "Suíça",
    "TLS": "Timor-Leste",
    "TUR": "Turquia",
    "UKR": "Ucrânia",
    "URY": "Uruguai",
    "VEN": "Venezuela",
}

for _code, _name in COMMON_COUNTRIES:
    if _code != "OTHER" and _code not in _ICAO_PT:
        _ICAO_PT[_code] = _name


@dataclass(frozen=True)
class CountryRow:
    code: str
    label: str
    entered: int
    slept: int
    dormidas: int


@dataclass(frozen=True)
class MonthReport:
    year: int
    month: int
    rows: list[CountryRow]
    guest_ids: list[str]
    skipped: list[str]
    no_movement: bool

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def total_entered(self) -> int:
        return sum(r.entered for r in self.rows)

    @property
    def total_slept(self) -> int:
        return sum(r.slept for r in self.rows)

    @property
    def total_dormidas(self) -> int:
        return sum(r.dormidas for r in self.rows)


def default_report_month(today: date | None = None) -> tuple[int, int]:
    """Previous calendar month in Lisbon (typical IPHH reference month)."""
    day = today or datetime.now(LISBON).date()
    if day.month == 1:
        return day.year - 1, 12
    return day.year, day.month - 1


def parse_year_month(text: str) -> tuple[int, int] | None:
    raw = str(text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})", raw)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if month < 1 or month > 12:
        return None
    return year, month


def country_label(code: str) -> str:
    key = str(code or "").strip().upper()
    if not key:
        return "(missing residence country)"
    if key == "ZZZ":
        return "Sem país (eviivo)"
    return _ICAO_PT.get(key, f"Other — {key}")


def nights_in_month(checkin: date, checkout: date, year: int, month: int) -> int:
    """Nights of [checkin, checkout) that fall inside the reference month."""
    if checkout <= checkin:
        return 0
    month_start = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    month_end = date(year, month, last_day)
    # exclusive end = day after last night in month
    next_day = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    start = max(checkin, month_start)
    end = min(checkout, next_day)
    if end <= start:
        return 0
    # Cap against calendar (defensive)
    _ = month_end
    return (end - start).days


def _parse_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def build_month_report(stays: list[dict], year: int, month: int) -> MonthReport:
    """Aggregate Quadro 1 by residence country for one month.

    Each stay is one eviivo booking. `guests` is adults + children (at least 1).
    Country comes from eviivo, or from the check-in form when that was filled in.
    """
    buckets: dict[str, list[int]] = {}
    guest_ids: list[str] = []
    skipped: list[str] = []
    missing_country = 0

    for row in stays:
        ref = str(row.get("reference") or row.get("id") or "")
        fields = dict(row.get("fields") or {})
        checkin = _parse_date(str(row.get("checkin") or fields.get("checkin_date") or ""))
        checkout = _parse_date(str(row.get("checkout") or fields.get("checkout_date") or ""))
        if checkin is None:
            skipped.append(f"{ref or '?'}: missing check-in date")
            continue
        if checkout is None:
            checkout = checkin.fromordinal(checkin.toordinal() + 1)

        nights = nights_in_month(checkin, checkout, year, month)
        if nights <= 0:
            continue

        try:
            people = int(row.get("guests") or 1)
        except (TypeError, ValueError):
            people = 1
        if people < 1:
            people = 1

        code = str(row.get("country") or fields.get("residence_country") or "").strip().upper()
        if not code:
            code = "ZZZ"
            missing_country += people
        entered = people if checkin.year == year and checkin.month == month else 0
        bucket = buckets.setdefault(code, [0, 0, 0])
        bucket[0] += entered
        bucket[1] += people
        bucket[2] += nights * people
        if ref:
            guest_ids.append(ref)

    if missing_country:
        skipped.append(
            f"{missing_country} guest(s) have no country in eviivo — shown as Sem país."
        )

    rows: list[CountryRow] = []
    for code, (entered, slept, dormidas) in buckets.items():
        rows.append(
            CountryRow(
                code=code,
                label=country_label(code),
                entered=entered,
                slept=slept,
                dormidas=dormidas,
            )
        )
    # PRT first, then label
    rows.sort(key=lambda r: (0 if r.code == "PRT" else 2 if r.code == "ZZZ" else 1, r.label.lower(), r.code))
    return MonthReport(
        year=year,
        month=month,
        rows=rows,
        guest_ids=guest_ids,
        skipped=skipped,
        no_movement=len(rows) == 0,
    )


def report_to_csv(report: MonthReport) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "country_code",
            "country_label",
            "guests_entered",
            "guests_slept",
            "dormidas",
        ]
    )
    for row in report.rows:
        writer.writerow([row.code, row.label, row.entered, row.slept, row.dormidas])
    writer.writerow(
        ["TOTAL", "", report.total_entered, report.total_slept, report.total_dormidas]
    )
    return buf.getvalue()
