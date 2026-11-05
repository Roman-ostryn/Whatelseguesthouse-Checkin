"""Load .env settings for check-in / SIBA / INE."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from paths import app_root

ROOT = app_root()


def load_settings() -> None:
    load_dotenv(ROOT / ".env")


def siba_settings() -> dict[str, str]:
    load_settings()
    estabelecimento = str(os.getenv("SIBA_ESTABELECIMENTO", "")).strip()
    if estabelecimento.isdigit():
        estabelecimento = f"{int(estabelecimento):02d}"
    return {
        "nif": str(os.getenv("SIBA_NIF", "")).strip(),
        "estabelecimento": estabelecimento,
        "chave": str(os.getenv("SIBA_CHAVE", "")).strip(),
        "wsdl": str(
            os.getenv(
                "SIBA_WSDL",
                "https://siba.ssi.gov.pt/baws/boletinsalojamento.asmx?wsdl",
            )
        ).strip(),
        "endpoint": str(
            os.getenv(
                "SIBA_ENDPOINT",
                "https://siba.ssi.gov.pt/baws/boletinsalojamento.asmx",
            )
        ).strip(),
        "hotel_name": str(os.getenv("SIBA_HOTEL_NAME", "Whatelse Guest House")).strip()[:40],
        "hotel_abbrev": str(os.getenv("SIBA_HOTEL_ABBREV", "Whatelse")).strip()[:15],
        "morada": str(os.getenv("SIBA_MORADA", "")).strip()[:40],
        "localidade": str(os.getenv("SIBA_LOCALIDADE", "Fatima")).strip()[:30],
        "codigo_postal": str(os.getenv("SIBA_CODIGO_POSTAL", "")).strip()[:4],
        "zona_postal": str(os.getenv("SIBA_ZONA_POSTAL", "")).strip()[:3],
        "telefone": str(os.getenv("SIBA_TELEFONE", "")).strip()[:9],
        "fax": str(os.getenv("SIBA_FAX", "")).strip()[:9] or "000000000",
        "nome_contacto": str(os.getenv("SIBA_NOME_CONTACTO", "Joao")).strip()[:40],
        "email_contacto": str(os.getenv("SIBA_EMAIL_CONTACTO", "")).strip()[:140],
    }


def siba_ready() -> str:
    """Empty string when SIBA can send; otherwise a short problem text."""
    cfg = siba_settings()
    if not cfg["nif"] or not cfg["nif"].isdigit() or len(cfg["nif"]) != 9:
        return "SIBA_NIF is missing or not 9 digits in .env."
    if not cfg["estabelecimento"]:
        return "SIBA_ESTABELECIMENTO is missing in .env."
    if not cfg["chave"]:
        return "SIBA_CHAVE is missing in .env."
    if not cfg["morada"] or not cfg["codigo_postal"] or not cfg["zona_postal"]:
        return "SIBA hotel address / postal code is incomplete in .env."
    if not cfg["telefone"] or not cfg["email_contacto"]:
        return "SIBA_TELEFONE or SIBA_EMAIL_CONTACTO is missing in .env."
    return ""


def ine_settings() -> dict[str, str]:
    load_settings()
    return {
        "user": str(os.getenv("INE_WEBINQ_USER", "")).strip(),
        "password": str(os.getenv("INE_WEBINQ_PASSWORD", "")).strip(),
        "url": str(os.getenv("INE_WEBINQ_URL", "https://webinq.ine.pt")).strip()
        or "https://webinq.ine.pt",
    }


def ine_ready() -> str:
    """Empty when WebInq login values exist (for staff reminder). Not used to auto-submit."""
    cfg = ine_settings()
    if not cfg["user"]:
        return "INE_WEBINQ_USER is missing in .env."
    if not cfg["password"]:
        return "INE_WEBINQ_PASSWORD is missing in .env."
    return ""


def link_settings() -> dict[str, str | int]:
    load_settings()
    days_raw = str(os.getenv("CHECKIN_LINK_DAYS_BEFORE", "15")).strip() or "15"
    try:
        days = max(0, int(days_raw))
    except ValueError:
        days = 15
    return {
        "public_base_url": str(os.getenv("CHECKIN_PUBLIC_BASE_URL", "")).strip().rstrip("/"),
        "days_before": days,
        "resend_api_key": str(os.getenv("RESEND_API_KEY", "")).strip(),
        "resend_from": str(os.getenv("RESEND_FROM", "")).strip(),
    }


def link_public_ready() -> str:
    """Empty when guest links can be opened from outside the hotel PC."""
    cfg = link_settings()
    if not cfg["public_base_url"]:
        return (
            "CHECKIN_PUBLIC_BASE_URL is empty — guests need a public HTTPS URL "
            "(ngrok / Railway / VPS) pointing at this app."
        )
    return ""


def link_mail_ready() -> str:
    cfg = link_settings()
    if not cfg["resend_api_key"] or not cfg["resend_from"]:
        return "Add RESEND_API_KEY and RESEND_FROM to .env to email guest links."
    return ""


def cron_secret() -> str:
    load_settings()
    return str(os.getenv("CRON_SECRET", "")).strip()


def cron_authorized(header_value: str = "", query_secret: str = "") -> bool:
    """True when CRON_SECRET is set and the request presents it."""
    expected = cron_secret()
    if not expected:
        return False
    auth = str(header_value or "").strip()
    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    candidate = auth or str(query_secret or "").strip()
    return bool(candidate) and candidate == expected
