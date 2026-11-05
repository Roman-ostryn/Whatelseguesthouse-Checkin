# Whatelse — online check-in (SIBA / AIMA + INE)

Replaces **CheckIn Scan**. Staff or guest fills check-in; foreign guests go to SIBA; monthly INE totals from `/ine`.

## Goal

1. Staff form and/or **guest link** (MRZ paste optional).
2. Store stays locally.
3. **SIBA / AIMA** — send Boletim (staff button, or **auto** after guest link submit).
4. **INE** — monthly IPHH Quadro 1 from stays (copy into WebInq).

## Credentials

Copy `.env.example` → `.env` on the hotel PC only. Never commit `.env`.

## Status

| Step | Status | What |
|------|--------|------|
| 1–4 | **Done** | Form, save, SIBA send |
| 5 | **Done** | INE Quadro 1 report |
| 6 | **Done** | MRZ paste fill |
| 7 | **Done** | Given name + check-out **required** |
| 8 | **Done** | Guest link `/g/<token>` + auto SIBA; hotel `/links` |
| 9 | Next | Auto-email links **15 days** before from eviivo (needs public URL + Resend) |

### Run (dev)

```text
cd checkin-automation
pip install -r requirements.txt
python run.py
```

### Hotel PC (exe)

```text
python build_exe.py
```

`dist/WhatelseCheckin/` — double-click **WhatelseCheckin.exe** (opens browser). Keep `.env` next to the exe.

### URLs (while running)

- Staff form: http://127.0.0.1:8787/checkin  
- List: http://127.0.0.1:8787/list  
- **Guest links:** http://127.0.0.1:8787/links  
- Guest form: `/g/<token>`  
- INE: http://127.0.0.1:8787/ine  

### Guest link (like CheckIn Scan)

1. Joao opens **/links**, creates a link (guest, email, check-in/out).  
2. Guest opens the URL, fills (or pastes MRZ), submits.  
3. Foreign guests → **auto SIBA**. Portuguese → save only.  
4. **15 days before:** target delay in `.env` as `CHECKIN_LINK_DAYS_BEFORE=15`.  
5. For guests off the hotel PC set `CHECKIN_PUBLIC_BASE_URL` to a public HTTPS URL (ngrok / Railway / VPS).  
6. Optional email: `RESEND_API_KEY` + `RESEND_FROM`, then **Email link** on `/links`.

### INE

Open `/ine`, pick month, copy Quadro 1 into WebInq by day 10. Not auto-submitted.

## Official links

- SIBA: https://siba.ssi.gov.pt  
- INE WebInq: https://webinq.ine.pt  
