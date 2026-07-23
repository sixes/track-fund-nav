track-southbound-connect
========================

Daily fund NAV monitor that sends Gmail alerts when a fund's return
vs your cost base crosses a new 5% band (5%, 10%, 15%, ...).

How it works
------------
1. Fetches latest NAV for each fund (Morningstar HK + Eastmoney API).
2. Calculates % change vs your recorded base price.
3. Buckets the change into 5% bands: 0-4.99% = band 0, 5-9.99% = band 1, etc.
4. Compares today's band to the last-saved band in state.json.
5. Sends an HTML-table email only when the band changes (crossing a new 5% line).
   - e.g. 4.5% -> 6% triggers (crossed into band 1).
   - 6% -> 9% does NOT trigger (still in band 1).
   - 9% -> 11% triggers (crossed into band 2).
   - Works symmetrically for declines (-5%, -10%, ...).

Files
-----
fetch_fund.py       Main script. Run daily via cron.
state.json          Persisted band state per fund (auto-created on first run).
fund_watcher.log    Rotating run log with table output.
.env                Credentials (not committed). Copy from .env.example.
.env.example        Template for .env.
requirements.txt    Python dependencies.

Tracked funds
-------------
ID              Name                Source
HK0001026985    Efund nasdaq100     Morningstar
HK0000615697    BocPru SP500        Morningstar
021778          GF nasdaq100        Eastmoney GZ
018967          99fund nasdaq100    Eastmoney GZ
023918          FCF                 Eastmoney GZ
005125          SP Div              Eastmoney HTML
019548          CMB Nasdaq100       Eastmoney GZ
019314          HKConnect           Eastmoney GZ
012860          EFund500            Eastmoney GZ
022903          FullgoalDivY        Eastmoney GZ
0P00000AWU      BLK World Tech      Morningstar Global (USD)
0P00000S19      JPM US Tech         Morningstar Global (USD)

Setup
-----
1. Create virtualenv and install dependencies:

       python3 -m venv .venv
       .venv/bin/pip install -r requirements.txt

2. Create .env from template and fill in credentials:

       cp .env.example .env

   Required variables:
     GMAIL_USER          Your Gmail address (sender).
     GMAIL_APP_PASSWORD  App password (not your login password).
                         Generate at https://myaccount.google.com/apppasswords
                         (requires 2-Step Verification enabled).
     GMAIL_TO            Recipient email (defaults to GMAIL_USER if omitted).
     HC_PING_URL         (Optional) Healthchecks.io ping URL for uptime monitoring.

3. Test manually:

       .venv/bin/python fetch_fund.py

   First run records current bands into state.json without emailing.
   Subsequent runs only email when a band boundary is newly crossed.

Cron
----
Installed at 18:00 daily (after HK market NAV publication):

    0 18 * * * cd /home/fec/track-southbound-connect && .venv/bin/python fetch_fund.py >> fetch_fund.log 2>&1

Email format
------------
- Subject: "Fund NAV update: GF nasdaq100 +12.7%, BocPru SP500 +11.2%"
- Body: HTML table with columns: Fund, Change vs base, Daily %, Latest NAV,
  Base, Band crossed (e.g. "5% -> 10%"), NAV date.
- Daily % is the day-over-day NAV change: exact for Morningstar Global funds
  (from their price series) and derived from the previous run's stored NAV for
  other sources (shows "-" on a fund's first run).
- Positive moves shown in green with triangle-up; negative in red with
  triangle-down.
- Plain-text fallback included for clients that don't render HTML.

Adding a new fund
-----------------
Add an entry to the FUNDS list in fetch_fund.py:

    {
        "id": "XXXXXX",
        "name": "Friendly name",
        "source": "eastmoney_gz",   # or "morningstar" / "eastmoney_html" / "morningstar_global"
        "base": 1.2345,             # your cost basis
    }

For "eastmoney_html" source, also supply "nav_xpath" and "date_xpath".
For "morningstar_global" source, "id" is the Morningstar SecId (e.g. 0P00000AWU);
optionally set "currency" (default "USD") and "universe" (default "FOGBR$$ALL").

Delete the fund's key from state.json (or the whole file) to re-initialize
its band tracking on the next run.

Resetting alerts
----------------
Delete state.json to re-initialize all funds. The next run will record
current bands silently; alerts resume from the run after that.

To force an alert for testing, edit state.json and set a fund's value to 0
(simulates "was inside +/-5%"), then run the script.
