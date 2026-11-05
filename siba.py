"""Send Boletim de Alojamento to SIBA / AIMA (foreign guests)."""

from __future__ import annotations

import base64
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from config import siba_ready, siba_settings

LISBON = ZoneInfo("Europe/Lisbon")
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
SEF_NS = "http://sef.pt/"
# Working public samples use sef.pt/BAws (no www).
BAL_NS = "http://sef.pt/BAws"


class SibaError(RuntimeError):
    pass


def is_foreign_guest(fields: dict) -> bool:
    nationality = str(fields.get("nationality") or "").strip().upper()
    return bool(nationality) and nationality != "PRT"


def send_checkin(fields: dict, *, file_number: int) -> str:
    """
    Send one guest boletim. Returns '0' on success.
    Raises SibaError on failure.
    """
    problem = siba_ready()
    if problem:
        raise SibaError(problem)
    if not is_foreign_guest(fields):
        raise SibaError("SIBA is only for foreign guests (nationality not PRT).")
    cfg = siba_settings()
    xml_body = build_movimento_xml(cfg, fields, file_number)
    encoded = base64.b64encode(xml_body.encode("utf-8")).decode("ascii")
    return _soap_deliver(
        endpoint=cfg["endpoint"],
        nif=cfg["nif"],
        estabelecimento=cfg["estabelecimento"],
        chave=cfg["chave"],
        boletins_b64=encoded,
    )


def build_movimento_xml(cfg: dict[str, str], fields: dict, file_number: int) -> str:
    """BAL XML (MovimentoBAL) before Base64 encoding."""
    surname = _name(fields.get("surname") or "")
    given = _name(fields.get("given_name") or "")
    if not surname and given:
        surname, given = given, ""
    if not surname:
        raise SibaError("Surname is required for SIBA.")

    birth = _xml_date(fields.get("birth_date") or "")
    entrada = _xml_date(fields.get("checkin_date") or "")
    saida = _xml_date(fields.get("checkout_date") or "") if fields.get("checkout_date") else ""
    if not birth or not entrada:
        raise SibaError("Birth date and check-in date are required for SIBA.")

    now = datetime.now(LISBON).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # BAL.XSD (targetNamespace http://sef.pt/BAws): strict sequence.
    # Error 75 was order (Tipo before Pais), not the field name.
    # Error 77 was wrong xmlns (www.sef.pt/BAws) so Unidade_Hoteleira was ignored.
    birth_place = _text(fields.get("birth_place") or "")[:30]
    residence_place = _text(fields.get("residence_place") or "")[:30] or "NA"
    boletim_parts: list[tuple[str, str]] = [
        ("Apelido", surname[:40]),
        ("Nome", (given or " ")[:40]),
        ("Nacionalidade", str(fields.get("nationality") or "").upper()[:3]),
        ("Data_Nascimento", birth),
    ]
    if birth_place:
        boletim_parts.append(("Local_Nascimento", birth_place))
    boletim_parts.extend(
        [
            ("Documento_Identificacao", _doc(fields.get("document_number") or "")[:16]),
            (
                "Pais_Emissor_Documento",
                str(fields.get("document_country") or "").upper()[:3],
            ),
            ("Tipo_Documento", str(fields.get("document_type") or "P").upper()[:3]),
            ("Data_Entrada", entrada),
        ]
    )
    if saida:
        boletim_parts.append(("Data_Saida", saida))
    boletim_parts.extend(
        [
            (
                "Pais_Residencia_Origem",
                str(fields.get("residence_country") or "").upper()[:3],
            ),
            ("Local_Residencia_Origem", residence_place),
        ]
    )

    estab = cfg["estabelecimento"]
    if estab.isdigit():
        estab = f"{int(estab):02d}"

    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<MovimentoBAL xmlns="{BAL_NS}">',
        "<Unidade_Hoteleira>",
        f"<Codigo_Unidade_Hoteleira>{escape(cfg['nif'])}</Codigo_Unidade_Hoteleira>",
        f"<Estabelecimento>{escape(estab)}</Estabelecimento>",
        f"<Nome>{escape(cfg['hotel_name'])}</Nome>",
        f"<Abreviatura>{escape(cfg['hotel_abbrev'])}</Abreviatura>",
        f"<Morada>{escape(cfg['morada'])}</Morada>",
        f"<Localidade>{escape(cfg['localidade'])}</Localidade>",
        f"<Codigo_Postal>{escape(cfg['codigo_postal'])}</Codigo_Postal>",
        f"<Zona_Postal>{escape(cfg['zona_postal'])}</Zona_Postal>",
        f"<Telefone>{escape(cfg['telefone'])}</Telefone>",
        f"<Fax>{escape(cfg['fax'])}</Fax>",
        f"<Nome_Contacto>{escape(cfg['nome_contacto'])}</Nome_Contacto>",
        f"<Email_Contacto>{escape(cfg['email_contacto'])}</Email_Contacto>",
        "</Unidade_Hoteleira>",
        "<Boletim_Alojamento>",
    ]
    for key, value in boletim_parts:
        parts.append(f"<{key}>{escape(value)}</{key}>")
    parts.extend(
        [
            "</Boletim_Alojamento>",
            "<Envio>",
            f"<Numero_Ficheiro>{int(file_number)}</Numero_Ficheiro>",
            f"<Data_Movimento>{now}</Data_Movimento>",
            "</Envio>",
            "</MovimentoBAL>",
        ]
    )
    # Compact XML like working public samples.
    return "".join(parts)


def _soap_deliver(
    *,
    endpoint: str,
    nif: str,
    estabelecimento: str,
    chave: str,
    boletins_b64: str,
) -> str:
    # Portal may show 0; SIBA samples use zero-padded "00".
    est = estabelecimento
    if est.isdigit():
        est = f"{int(est):02d}"
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
 xmlns:soap="{SOAP_NS}">
  <soap:Body>
    <EntregaBoletinsAlojamento xmlns="{SEF_NS}">
      <UnidadeHoteleira>{escape(nif)}</UnidadeHoteleira>
      <Estabelecimento>{escape(est)}</Estabelecimento>
      <ChaveAcesso>{escape(chave)}</ChaveAcesso>
      <Boletins>{boletins_b64}</Boletins>
    </EntregaBoletinsAlojamento>
  </soap:Body>
</soap:Envelope>
"""
    req = urllib.request.Request(
        endpoint,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://sef.pt/EntregaBoletinsAlojamento",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SibaError(f"SIBA HTTP {exc.code}. {detail}") from None
    except urllib.error.URLError as exc:
        raise SibaError(f"SIBA connection failed. {exc.reason}") from None

    result = _parse_soap_result(raw)
    if result == "0":
        return result
    raise SibaError(_format_siba_error(result))


def _parse_soap_result(raw: str) -> str:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SibaError(f"SIBA returned invalid XML. {exc}") from None
    for tag in (
        f".//{{{SEF_NS}}}EntregaBoletinsAlojamentoResult",
        ".//{http://sef.pt/}EntregaBoletinsAlojamentoResult",
        ".//EntregaBoletinsAlojamentoResult",
    ):
        node = root.find(tag)
        if node is not None and node.text is not None:
            return node.text.strip()
    # Fallback: any *Result text
    for node in root.iter():
        if node.tag.endswith("EntregaBoletinsAlojamentoResult") and node.text:
            return node.text.strip()
    raise SibaError(f"SIBA response had no result. {raw[:300]}")


def _format_siba_error(result: str) -> str:
    text = result.strip()
    if text == "0":
        return "OK"
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
            code = (
                root.findtext(".//Codigo_Retorno")
                or root.findtext(".//{http://sef.pt/}Codigo_Retorno")
                or root.findtext(".//{http://www.sef.pt/BAws}Codigo_Retorno")
            )
            desc = (
                root.findtext(".//Descricao")
                or root.findtext(".//{http://sef.pt/}Descricao")
                or root.findtext(".//{http://www.sef.pt/BAws}Descricao")
            )
            if str(code) == "77":
                return (
                    "SIBA error 77: hotel-unit block not recognized in the XML "
                    "(wrong xmlns or missing Unidade_Hoteleira). "
                    f"Detail: {desc or text[:200]}"
                )
            if code or desc:
                return f"SIBA refused the boletim ({code or '?'}): {desc or text[:200]}"
        except ET.ParseError:
            pass
    return f"SIBA refused the boletim: {text[:300]}"


def _xml_date(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00.000Z"
    if re.fullmatch(r"\d{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}T00:00:00.000Z"
    return ""


def _name(value: str) -> str:
    text = " ".join(str(value or "").upper().split())
    return re.sub(r"[^A-ZÇÃÁÀÂÉÊÍÕÔÓÚÜ'\-\s]", "", text)


def _text(value: str) -> str:
    text = " ".join(str(value or "").upper().split())
    return re.sub(r"[^A-Z0-9ÇÃÁÀÂÉÊÍÕÔÓÚÜ'\-\s,\.]", "", text)


def _doc(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()
