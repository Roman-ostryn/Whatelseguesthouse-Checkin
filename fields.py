"""Check-in form fields aligned with SIBA Boletim de Alojamento."""

from __future__ import annotations

# Common ICAO 9303 country codes for the form. Guests can still type another code.
COMMON_COUNTRIES: list[tuple[str, str]] = [
    ("PRT", "Portugal"),
    ("ESP", "Spain"),
    ("FRA", "France"),
    ("DEU", "Germany"),
    ("GBR", "United Kingdom"),
    ("ITA", "Italy"),
    ("NLD", "Netherlands"),
    ("BEL", "Belgium"),
    ("BRA", "Brazil"),
    ("USA", "United States"),
    ("POL", "Poland"),
    ("IRL", "Ireland"),
    ("CHE", "Switzerland"),
    ("AUT", "Austria"),
    ("UKR", "Ukraine"),
    ("ROU", "Romania"),
    ("CHN", "China"),
    ("JPN", "Japan"),
    ("KOR", "South Korea"),
    ("CAN", "Canada"),
    ("AUS", "Australia"),
    ("IND", "India"),
    ("OTHER", "Other (type code below)"),
]

DOC_TYPES: list[tuple[str, str]] = [
    ("P", "Passport"),
    ("B", "Identity card (BI / CC)"),
    ("O", "Other"),
]

# name -> (required, label)
FIELD_SPEC: list[tuple[str, bool, str]] = [
    ("surname", True, "Surname (Apelido)"),
    ("given_name", True, "Given name (Nome)"),
    ("nationality", True, "Nationality (ICAO code)"),
    ("birth_date", True, "Date of birth"),
    ("birth_place", False, "Place of birth"),
    ("document_number", True, "Document number"),
    ("document_type", True, "Document type"),
    ("document_country", True, "Document issuing country"),
    ("residence_country", True, "Country of residence"),
    ("residence_place", False, "Place of residence"),
    ("checkin_date", True, "Check-in date"),
    ("checkout_date", True, "Check-out date"),
    ("email", False, "Email"),
    ("phone", False, "Phone"),
    ("room", False, "Room (optional)"),
    ("notes", False, "Notes for the hotel"),
]
