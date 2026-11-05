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
| 9 | **Done** | Auto-email links from eviivo (today → N days before; default 15) |

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
4. **Auto email (Joao):** set eviivo + Resend + `CHECKIN_PUBLIC_BASE_URL`.  
   Job emails Whatelse stays with check-in from **today through** `CHECKIN_LINK_DAYS_BEFORE` (default 15), once per booking.  
   Manual **/links** create/email/remove still works.  
5. **Daily cron:** set `CRON_SECRET`, then GET/POST once a day (Lisbon morning):  
   `https://YOUR-app.up.railway.app/cron/send-due-links?secret=YOUR_CRON_SECRET`  
   Or use header `Authorization: Bearer YOUR_CRON_SECRET`.  
   Staff can also click **Run auto emails now** on `/links`.  
6. CLI: `python send_due.py` (add `--dry-run` to preview).

### INE

Open `/ine`, pick month, copy Quadro 1 into WebInq by day 10. Not auto-submitted.

## Official links

- SIBA: https://siba.ssi.gov.pt  
- INE WebInq: https://webinq.ine.pt  
