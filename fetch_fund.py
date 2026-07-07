#!/usr/bin/env python3
"""Fetch fund NAVs and alert via Gmail on new 5% band crossings."""

import json
import logging
import math
import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from lxml import html

MORNINGSTAR_BASE = "https://zlglobal.morningstar.cn"
EASTMONEY_GZ = "http://fundgz.1234567.com.cn/js/{code}.js"

FUNDS = [
    {
        "id": "HK0001026985",
        "name": "Efund nasdaq100",
        "source": "morningstar",
        "base": 11.92,
    },
    {
        "id": "HK0000615697",
        "name": "BocPru SP500",
        "source": "morningstar",
        "base": 2.74,
    },
    {
        "id": "021778",
        "name": "GF nasdaq100",
        "source": "eastmoney_gz",
        "base": 7.2360,
    },
    {
        "id": "018967",
        "name": "99fund nasdaq100",
        "source": "eastmoney_gz",
        "base": 1.3747,
    },
    {
        "id": "023918",
        "name": "FCF",
        "source": "eastmoney_gz",
        "base": 1.3738,
    },
    {
        "id": "005125",
        "name": "SP Div",
        "source": "eastmoney_html",
        "base": 1.827,
        "nav_xpath": '//*[@id="body"]/div[11]/div/div/div[2]/div[1]/div[1]/dl[1]/dd[1]/span[1]',
        "date_xpath": '//*[@id="body"]/div[11]/div/div/div[2]/div[1]/div[1]/dl[1]/dt/p',
    },
    {
        "id": "019548",
        "name": "CMB Nasdaq100",
        "source": "eastmoney_gz",
        "base": 1.3717,
    },
    {
        "id": "019314",
        "name": "HKConnect",
        "source": "eastmoney_gz",
        "base": 1.1601,
    },
    {
        "id": "012860",
        "name": "EFund500",
        "source": "eastmoney_gz",
        "base": 2.8157,
    },
    {
        "id": "022903",
        "name": "FullgoalDivY",
        "source": "eastmoney_gz",
        "base": 0.9795,
    },
]

BAND = 0.05
STATE_FILE = Path(__file__).with_name("state.json")
LOG_FILE = Path(__file__).with_name("fund_watcher.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

MORNINGSTAR_NAV_XPATH = '//*[@id="fundInfo"]/tbody/tr[2]/td[1]/span'
MORNINGSTAR_DATE_XPATH = '//*[@id="fundInfo"]/tbody/tr[4]/td[1]'
JSONP_RE = re.compile(r"jsonpgz\((\{.*\})\)")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def fetch_morningstar(session: requests.Session, fund: dict) -> dict:
    url = f"{MORNINGSTAR_BASE}/FundDetail/Overview?id={fund['id']}"
    session.get(url, timeout=30)
    session.get(
        f"{MORNINGSTAR_BASE}/api/GlobalApi/SetLogin",
        headers={"Referer": url},
        timeout=30,
    )
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    tree = html.fromstring(resp.text)
    nav_nodes = tree.xpath(MORNINGSTAR_NAV_XPATH)
    date_nodes = tree.xpath(MORNINGSTAR_DATE_XPATH)
    return {
        "nav": nav_nodes[0].text_content().strip() if nav_nodes else None,
        "nav_date": date_nodes[0].text_content().strip() if date_nodes else None,
    }


def fetch_eastmoney_gz(session: requests.Session, fund: dict) -> dict:
    resp = session.get(
        EASTMONEY_GZ.format(code=fund["id"]),
        headers={"Referer": "http://fund.eastmoney.com/"},
        timeout=15,
    )
    resp.raise_for_status()
    match = JSONP_RE.search(resp.text)
    if not match:
        return {"nav": None, "nav_date": None}
    payload = json.loads(match.group(1))
    return {"nav": payload.get("dwjz"), "nav_date": payload.get("jzrq")}


def fetch_eastmoney_html(session: requests.Session, fund: dict) -> dict:
    url = f"http://fund.eastmoney.com/{fund['id']}.html"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    tree = html.fromstring(resp.text)
    nav_nodes = tree.xpath(fund["nav_xpath"])
    date_nodes = tree.xpath(fund["date_xpath"])
    return {
        "nav": nav_nodes[0].text_content().strip() if nav_nodes else None,
        "nav_date": date_nodes[0].text_content().strip() if date_nodes else None,
    }


SOURCES = {
    "morningstar": fetch_morningstar,
    "eastmoney_gz": fetch_eastmoney_gz,
    "eastmoney_html": fetch_eastmoney_html,
}


def fetch_fund(session: requests.Session, fund: dict) -> dict:
    handler = SOURCES[fund["source"]]
    info = handler(session, fund)
    nav_date = info["nav_date"]
    if nav_date:
        m = DATE_RE.search(nav_date)
        nav_date = m.group(0) if m else nav_date
    return {
        "fund_id": fund["id"],
        "name": fund["name"],
        "nav": info["nav"],
        "nav_date": nav_date,
    }


def send_gmail(subject: str, body: str, html_body: Optional[str] = None) -> bool:
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        log.warning("GMAIL_USER / GMAIL_APP_PASSWORD not set; skipping email.")
        return False
    recipient = os.environ.get("GMAIL_TO", user)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    return True


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError) as exc:
        log.error(f"state load failed: {exc}")
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def band_step(change: float) -> int:
    # Signed count of full 5% bands away from base.
    # 4.5% -> 0, 6% -> 1, 9% -> 1, 11% -> 2, -3% -> 0, -6% -> -1.
    if change >= 0:
        return math.floor(change / BAND)
    return -math.floor(-change / BAND)


def build_email(alerts: list) -> tuple:
    subject_bits = [f"{a['name']} {a['change'] * 100:+.1f}%" for a in alerts]
    subject = "Fund NAV update: " + ", ".join(subject_bits)

    text_lines = ["Hi,", "", "Heads up on the funds you're tracking:", ""]
    for a in alerts:
        text_lines.append(
            f"- {a['name']} ({a['fund_id']}): {a['change'] * 100:+.2f}% vs base "
            f"(NAV {a['nav']} / base {a['base']}), crossed into {a['band_pct']}% band "
            f"from {a['prev_band_pct']}%. NAV date: {a['nav_date']}"
        )
    text_lines += ["", "No further alerts until the next 5% band is crossed.", "", "Fund watcher"]
    text_body = "\n".join(text_lines)

    rows = []
    for a in alerts:
        pct = a["change"] * 100
        color = "#137333" if pct >= 0 else "#c5221f"
        arrow = "&#9650;" if pct >= 0 else "&#9660;"
        band_cell = f"{a['prev_band_pct']}% &rarr; {a['band_pct']}%"
        rows.append(
            "<tr>"
            f"<td style=\"padding:8px 12px;border-bottom:1px solid #eee;\"><b>{a['name']}</b>"
            f"<div style=\"color:#666;font-size:12px;\">{a['fund_id']}</div></td>"
            f"<td style=\"padding:8px 12px;border-bottom:1px solid #eee;color:{color};"
            f"font-weight:600;white-space:nowrap;\">{arrow} {pct:+.2f}%</td>"
            f"<td style=\"padding:8px 12px;border-bottom:1px solid #eee;\">{a['nav']}</td>"
            f"<td style=\"padding:8px 12px;border-bottom:1px solid #eee;color:#666;\">{a['base']}</td>"
            f"<td style=\"padding:8px 12px;border-bottom:1px solid #eee;\">{band_cell}</td>"
            f"<td style=\"padding:8px 12px;border-bottom:1px solid #eee;color:#666;\">{a['nav_date']}</td>"
            "</tr>"
        )

    header = (
        "<tr style=\"background:#f5f6f8;text-align:left;font-size:12px;"
        "text-transform:uppercase;letter-spacing:0.5px;color:#555;\">"
        "<th style=\"padding:8px 12px;\">Fund</th>"
        "<th style=\"padding:8px 12px;\">Change vs base</th>"
        "<th style=\"padding:8px 12px;\">Latest NAV</th>"
        "<th style=\"padding:8px 12px;\">Base</th>"
        "<th style=\"padding:8px 12px;\">Band crossed</th>"
        "<th style=\"padding:8px 12px;\">NAV date</th>"
        "</tr>"
    )

    html_body = (
        "<!doctype html><html><body style=\"font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        "color:#222;font-size:14px;line-height:1.5;\">"
        "<p>Hi,</p>"
        f"<p>{len(alerts)} of your tracked fund(s) just crossed a new 5% band vs base.</p>"
        "<table style=\"border-collapse:collapse;border:1px solid #e0e0e0;"
        "font-size:14px;margin:12px 0;\">"
        f"<thead>{header}</thead><tbody>{''.join(rows)}</tbody>"
        "</table>"
        "<p style=\"color:#666;font-size:12px;\">You won't hear from me again on these "
        "funds until the next 5% band is crossed.</p>"
        "<p style=\"color:#666;font-size:12px;\">&mdash; Fund watcher</p>"
        "</body></html>"
    )

    return subject, text_body, html_body


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
    })

    log.info("=" * 60)
    log.info("Fund watcher run started")
    log.info("=" * 60)

    state = load_state()
    alerts = []
    rows = []

    for fund in FUNDS:
        fund_id = fund["id"]
        base = fund["base"]
        name = fund["name"]
        try:
            info = fetch_fund(session, fund)
        except Exception as exc:
            log.error(f"{name} ({fund_id}): fetch failed: {exc}")
            rows.append((name, fund_id, "ERR", "-", "-", "-", "-"))
            continue

        nav_str = info["nav"]
        if not nav_str:
            log.warning(f"{name} ({fund_id}): NAV is None")
            rows.append((name, fund_id, "N/A", "-", "-", "-", info["nav_date"] or "-"))
            continue

        try:
            nav = float(nav_str)
        except ValueError:
            log.error(f"{name} ({fund_id}): cannot parse NAV {nav_str!r}")
            rows.append((name, fund_id, nav_str, "-", "-", "-", info["nav_date"] or "-"))
            continue

        change = (nav - base) / base
        step = band_step(change)
        prev_step = state.get(fund_id)

        change_str = f"{change * 100:+.2f}%"
        band_str = f"{step * 5}%"
        status = ""

        if prev_step is None:
            state[fund_id] = step
            status = "NEW"
        elif step != prev_step and abs(step) >= 1:
            status = "ALERT"
            alerts.append({
                "name": name,
                "fund_id": fund_id,
                "change": change,
                "nav": nav,
                "base": base,
                "band_pct": step * 5,
                "prev_band_pct": prev_step * 5,
                "nav_date": info["nav_date"],
            })
            state[fund_id] = step
        elif step != prev_step:
            state[fund_id] = step
            status = "OK"
        else:
            status = "OK"

        rows.append((name, fund_id, f"{nav:.4f}", f"{base:.4f}", change_str, band_str, info["nav_date"] or "-", status))

    # Pretty table output
    headers = ("Fund", "ID", "NAV", "Base", "Change", "Band", "Date", "Status")
    col_widths = [max(len(str(row[i])) for row in rows + [headers]) for i in range(len(headers))]
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, col_widths)) + " |"

    log.info("")
    log.info(sep)
    log.info(header_line)
    log.info(sep)
    for row in rows:
        line = "| " + " | ".join(str(col).ljust(w) for col, w in zip(row, col_widths)) + " |"
        log.info(line)
    log.info(sep)
    log.info("")

    if alerts:
        subject, text_body, html_body = build_email(alerts)
        sent = send_gmail(subject, text_body, html_body)
        if sent:
            log.info(f"Alert email sent ({len(alerts)} fund(s) crossed a band)")
        else:
            log.warning(f"Alerts triggered but email skipped ({len(alerts)} fund(s))")
        for a in alerts:
            log.info(f"  ALERT: {a['name']} {a['change'] * 100:+.2f}% -> {a['band_pct']}% band")
    else:
        log.info("No new band crossings.")

    save_state(state)
    log.info("State saved. Done.")
    return 0


if __name__ == "__main__":
    load_dotenv()
    hc_url = os.environ.get("HC_PING_URL")
    try:
        rc = main()
        if hc_url:
            requests.get(hc_url, timeout=10)
        sys.exit(rc)
    except Exception as exc:
        log.exception(f"Fatal error: {exc}")
        if hc_url:
            requests.get(f"{hc_url}/fail", timeout=10)
        sys.exit(1)
