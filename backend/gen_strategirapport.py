#!/usr/bin/env python3
"""
gen_strategirapport.py — strategi-sammenligningsrapport (PDF) på FAKTISKE handler.
═══════════════════════════════════════════════════════════════════════════════════
Trækker rigtige, lukkede handler fra journalen (trading_dash.db) for et valgbart
datointerval og bygger samme layout som mock'en: equity-kurver, bar-paneler, nøgletal-
tabel, exit-årsager og P&L pr. måned — for de fire strategier + samlet.

READ-ONLY mod journalen (sqlite mode=ro). Datakilde-noter:
  - Sources: "Konfluens 2", "Europa-reversion", "BuyTheDip", "Trend Join Long".
  - Filtrér på EXIT-dato (realiseret P&L) i [start, end]. Ekskludér reconcile_phantom
    (ren bogføring, intet reelt udfald). Alle øvrige lukkede handler tæller med.
  - Holdetid fra duration_sec. Exit-årsager mappes til kategorier (dynamiske rækker).
  - Strategier uden handler i perioden vises med nuller (fx TJL før den er kørt).

Kør (på den maskine hvis journal har handlerne — typisk algoserveren):
    python gen_strategirapport.py --start 2026-03-02 --end 2026-06-27
    python gen_strategirapport.py                      # default: seneste 120 dage
    python gen_strategirapport.py --db trading_dash.db --out rapport.pdf --account "DUO509856 (paper)"

Placering: C:\\Projects\\trading_dash\\backend\\gen_strategirapport.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, KeepTogether)
from reportlab.lib.enums import TA_LEFT

# ── Strategier: (journal-source, vist navn, kort, farve) ─────────────────────
STRATS_CFG = [
    {"source": "Konfluens 2",      "name": "Konfluens 2",     "short": "K2",    "color": "#2563eb"},
    {"source": "Europa-reversion", "name": "EUREVERSION",     "short": "EUREV", "color": "#059669"},
    {"source": "BuyTheDip",        "name": "BuyTheDip",       "short": "BTD",   "color": "#d97706"},
    {"source": "Trend Join Long",  "name": "Trend Join Long", "short": "TJL",   "color": "#7c3aed"},
]
STRATS      = [c["name"] for c in STRATS_CFG]
SOURCE_OF   = {c["name"]: c["source"] for c in STRATS_CFG}
SHORT       = {c["name"]: c["short"] for c in STRATS_CFG}
COLORS      = {c["name"]: c["color"] for c in STRATS_CFG}
COLORS["Samlet"] = "#0f172a"

DK_MONTHS = ["jan","feb","mar","apr","maj","jun","jul","aug","sep","okt","nov","dec"]

# Exit-årsags-kategorier (vist rækkefølge; ukendte ender i "Andet")
CAT_ORDER = ["Mål", "Trailing", "Breakeven", "Revert", "Stop", "Tvangsluk",
             "Reconcile", "Manuelt stop", "Andet"]


def exit_category(reason: str) -> str:
    r = (reason or "").lower()
    if r == "target":                      return "Mål"
    if "trail" in r:                       return "Trailing"
    if r in ("breakeven", "be"):           return "Breakeven"
    if r == "revert":                      return "Revert"
    if r == "stop":                        return "Stop"
    if ("force" in r or "market_close" in r or "session_end" in r
            or "session_close" in r or "eod" in r):  return "Tvangsluk"
    if "reconcile" in r:                   return "Reconcile"
    if "stoppet" in r or "manuel" in r:    return "Manuelt stop"
    return "Andet"


# ── Datalæsning (read-only) ──────────────────────────────────────────────────
def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        try:
            return datetime.fromisoformat(str(s)[:26])
        except ValueError:
            return None


def load_trades(db_path, start, end):
    """{display_name: DataFrame(day, pnl, hold(min), win, reason(kategori))} for lukkede
    handler hvis EXIT-dato ligger i [start, end]. reconcile_phantom ekskluderes."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT source, exit_time_et, entry_time_et, pnl, duration_sec, exit_reason "
            "FROM trades WHERE exit_time_utc IS NOT NULL").fetchall()
    finally:
        con.close()
    src_set = set(SOURCE_OF.values())
    buckets = {name: [] for name in STRATS}
    for source, exit_et, entry_et, pnl, dur, reason in rows:
        if source not in src_set:
            continue
        if (reason or "").lower() == "reconcile_phantom":
            continue
        dt = _parse_dt(exit_et) or _parse_dt(entry_et)
        if dt is None:
            continue
        d = dt.date()
        if d < start or d > end:
            continue
        name = next(n for n, s in SOURCE_OF.items() if s == source)
        pnl = float(pnl or 0.0)
        buckets[name].append({
            "day": d, "pnl": round(pnl, 2),
            "hold": (float(dur) / 60.0) if dur else 0.0,
            "win": pnl > 0, "reason": exit_category(reason)})
    return {name: pd.DataFrame(rows_,
            columns=["day", "pnl", "hold", "win", "reason"]) for name, rows_ in buckets.items()}


def business_days(d0, d1):
    return [d.date() for d in pd.bdate_range(d0, d1)]


def daily_curve(df, days):
    daily = pd.Series(0.0, index=pd.Index(days, name="day"))
    if len(df):
        s = df.groupby("day")["pnl"].sum()
        # behold kun dage indenfor aksen
        s = s[s.index.isin(days)]
        daily.loc[s.index] = s.values
    return daily, daily.cumsum()


def max_drawdown(cum, notional):
    eq = notional + cum.values
    if len(eq) == 0:
        return 0.0, 0.0
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    i = int(np.argmin(dd))
    dd_abs = float(dd[i])
    dd_pct = float(dd[i] / peak[i] * 100) if peak[i] else 0.0
    return dd_abs, dd_pct


def metrics(df, daily, cum, notional):
    if not len(df):
        return dict(trades=0, wins=0, losses=0, wr=0, pnl=0.0, pf=0.0, avg_win=0.0,
                    avg_loss=0.0, payoff=0.0, expect=0.0, best_trade=0.0, worst_trade=0.0,
                    dd_abs=0.0, dd_pct=0.0, avg_hold=0.0, best_day=0.0, worst_day=0.0, sharpe=0.0)
    wins = df[df.win]["pnl"]
    losses = df[~df.win]["pnl"]
    gross_w = wins.sum()
    gross_l = abs(losses.sum())
    pf = (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
    aw = wins.mean() if len(wins) else 0.0
    al = losses.mean() if len(losses) else 0.0
    dd_abs, dd_pct = max_drawdown(cum, notional)
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    return dict(
        trades=len(df), wins=len(wins), losses=len(losses),
        wr=len(wins) / len(df) * 100,
        pnl=df["pnl"].sum(), pf=pf, avg_win=aw, avg_loss=al,
        payoff=(aw / abs(al)) if al else 0.0,
        expect=df["pnl"].mean(),
        best_trade=df["pnl"].max(), worst_trade=df["pnl"].min(),
        dd_abs=dd_abs, dd_pct=dd_pct,
        avg_hold=df["hold"].mean(),
        best_day=daily.max(), worst_day=daily.min(),
        sharpe=sharpe)


def pf_str(pf):
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def money2(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def pct(v):
    return f"{v:.1f}%"


# ── Figurer ──────────────────────────────────────────────────────────────────
def build_figures(days, cums, comb_cum, mets, comb_met, out_dir):
    plt.rcParams.update({"font.size": 9, "font.family": "DejaVu Sans"})
    x = [datetime.combine(d, datetime.min.time()) for d in days]

    fig, ax = plt.subplots(figsize=(7.4, 3.5), dpi=150)
    for s in STRATS:
        ax.plot(x, cums[s].values, color=COLORS[s], lw=1.6, label=s)
    ax.plot(x, comb_cum.values, color=COLORS["Samlet"], lw=2.6, label="Samlet", zorder=5)
    ax.axhline(0, color="#94a3b8", lw=0.8, ls="--")
    ax.set_ylabel("Akkumuleret P&L (USD)")
    ax.set_title("Akkumuleret P&L pr. strategi", loc="left", fontweight="bold", fontsize=11)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: DK_MONTHS[mdates.num2date(v).month - 1] if x and v >= x[0].toordinal() else ""))
    ax.grid(True, axis="y", color="#e2e8f0", lw=0.7)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2)
    fig.tight_layout()
    p_eq = out_dir / "fig_equity.png"
    fig.savefig(p_eq, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.4), dpi=150)
    labels = [SHORT[s] for s in STRATS]
    cols = [COLORS[s] for s in STRATS]

    def barpanel(ax, vals, title, fmt):
        bars = ax.bar(range(len(STRATS)), vals, color=cols, width=0.66)
        ax.set_title(title, fontsize=9.5, fontweight="bold")
        ax.set_xticks(range(len(STRATS)))
        ax.set_xticklabels(labels, fontsize=8.5)
        ax.grid(True, axis="y", color="#eef2f7", lw=0.6)
        for sp in ["top", "right"]:
            ax.spines[sp].set_visible(False)
        span = (max(vals) - min(0, min(vals))) or 1.0
        for b, v, lbl in zip(bars, vals, [fmt(v) for v in vals]):
            ax.text(b.get_x() + b.get_width()/2, v + span*0.02, lbl,
                    ha="center", va="bottom", fontsize=7.5, fontweight="bold")
        ax.margins(y=0.18)

    # PF: erstat inf med en endelig søjlehøjde, men vis "∞" som label
    pf_raw = [mets[s]["pf"] for s in STRATS]
    pf_fin = [p for p in pf_raw if p != float("inf")]
    pf_cap = (max(pf_fin) * 1.15) if pf_fin else 1.0
    pf_bar = [(pf_cap if p == float("inf") else p) for p in pf_raw]

    barpanel(axes[0], [mets[s]["pnl"] for s in STRATS], "Samlet P&L (USD)", lambda v: f"${v:,.0f}")
    barpanel(axes[1], [mets[s]["wr"] for s in STRATS], "Win rate (%)", lambda v: f"{v:.0f}%")
    bars = axes[2].bar(range(len(STRATS)), pf_bar, color=cols, width=0.66)
    axes[2].set_title("Profit factor", fontsize=9.5, fontweight="bold")
    axes[2].set_xticks(range(len(STRATS))); axes[2].set_xticklabels(labels, fontsize=8.5)
    axes[2].grid(True, axis="y", color="#eef2f7", lw=0.6)
    for sp in ["top", "right"]:
        axes[2].spines[sp].set_visible(False)
    for b, raw in zip(bars, pf_raw):
        axes[2].text(b.get_x()+b.get_width()/2, b.get_height()+pf_cap*0.02, pf_str(raw),
                     ha="center", va="bottom", fontsize=7.5, fontweight="bold")
    axes[2].margins(y=0.18)
    fig.tight_layout()
    p_bar = out_dir / "fig_bars.png"
    fig.savefig(p_bar, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p_eq, p_bar


# ── PDF ──────────────────────────────────────────────────────────────────────
def build_pdf(out_pdf, p_eq, p_bar, days, start, end, account, trades, mets, comb_met, notional):
    styles = getSampleStyleSheet()
    H_TITLE = ParagraphStyle("t", parent=styles["Title"], fontSize=19, textColor=colors.white,
                             alignment=TA_LEFT, spaceAfter=2, leading=22)
    H_SUB = ParagraphStyle("s", parent=styles["Normal"], fontSize=9,
                           textColor=colors.HexColor("#cbd5e1"), alignment=TA_LEFT)
    H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5,
                        textColor=colors.HexColor("#0f172a"), spaceBefore=10, spaceAfter=6)
    NOTE = ParagraphStyle("n", parent=styles["Normal"], fontSize=7.5,
                          textColor=colors.HexColor("#64748b"), leading=10)
    CAP = ParagraphStyle("c", parent=styles["Normal"], fontSize=8,
                         textColor=colors.HexColor("#475569"), spaceBefore=2, spaceAfter=6)

    DARK = colors.HexColor("#0f172a"); GRID = colors.HexColor("#e2e8f0")
    HEADBG = colors.HexColor("#0f172a"); ZEBRA = colors.HexColor("#f8fafc")
    POS = colors.HexColor("#047857"); NEG = colors.HexColor("#b91c1c")

    story = []
    banner = Table([[Paragraph("STRATEGIRAPPORT", H_TITLE)],
                    [Paragraph("Sammenligning af aktive strategier", H_SUB)]], colWidths=[180*mm])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), HEADBG),
        ("LEFTPADDING", (0,0), (-1,-1), 12), ("RIGHTPADDING", (0,0), (-1,-1), 12),
        ("TOPPADDING", (0,0), (0,0), 12), ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,1), (0,1), 0)]))
    story.append(banner)

    per_d = f"{start.day}. {DK_MONTHS[start.month-1]} {start.year}"
    per_d2 = f"{end.day}. {DK_MONTHS[end.month-1]} {end.year}"
    meta = Table([[
        Paragraph(f"<b>Periode:</b> {per_d} – {per_d2} &nbsp;·&nbsp; {len(days)} handelsdage", CAP),
        Paragraph(f"<b>Konto:</b> {account} &nbsp;·&nbsp; <b>Genereret:</b> {datetime.now():%Y-%m-%d %H:%M}", CAP),
    ]], colWidths=[95*mm, 85*mm])
    meta.setStyle(TableStyle([("ALIGN",(1,0),(1,0),"RIGHT"), ("LEFTPADDING",(0,0),(-1,-1),0),
                              ("RIGHTPADDING",(0,0),(-1,-1),0), ("TOPPADDING",(0,0),(-1,-1),4)]))
    story.append(meta)

    pnl_col = "#" + (POS if comb_met["pnl"] >= 0 else NEG).hexval()[2:]
    kpi = Table([[
        Paragraph(f"<font size=8 color='#64748b'>SAMLET P&amp;L</font><br/><font size=16 color='{pnl_col}'><b>{money2(comb_met['pnl'])}</b></font>", CAP),
        Paragraph(f"<font size=8 color='#64748b'>HANDLER I ALT</font><br/><font size=16 color='#0f172a'><b>{comb_met['trades']}</b></font>", CAP),
        Paragraph(f"<font size=8 color='#64748b'>SAMLET WIN RATE</font><br/><font size=16 color='#0f172a'><b>{pct(comb_met['wr'])}</b></font>", CAP),
        Paragraph(f"<font size=8 color='#64748b'>PROFIT FACTOR</font><br/><font size=16 color='#0f172a'><b>{pf_str(comb_met['pf'])}</b></font>", CAP),
    ]], colWidths=[45*mm]*4)
    kpi.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), ZEBRA), ("BOX", (0,0), (-1,-1), 0.5, GRID),
        ("INNERGRID", (0,0), (-1,-1), 0.5, GRID),
        ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING", (0,0), (-1,-1), 10), ("VALIGN", (0,0), (-1,-1), "MIDDLE")]))
    story.append(Spacer(1, 8)); story.append(kpi); story.append(Spacer(1, 10))

    story.append(Image(str(p_eq), width=180*mm, height=85*mm))
    story.append(Spacer(1, 6))
    story.append(Image(str(p_bar), width=180*mm, height=58*mm))
    story.append(Paragraph("K2 = Konfluens 2 &nbsp;·&nbsp; EUREV = EUREVERSION &nbsp;·&nbsp; "
                           "BTD = BuyTheDip &nbsp;·&nbsp; TJL = Trend Join Long", NOTE))
    story.append(PageBreak())

    # ── Tabel 1: nøgletal ──
    story.append(Paragraph("Nøgletal pr. strategi", H2))
    rows_def = [
        ("Antal handler",        lambda m: str(m["trades"])),
        ("Vindere",              lambda m: str(m["wins"])),
        ("Tabere",               lambda m: str(m["losses"])),
        ("Win rate",             lambda m: pct(m["wr"])),
        ("Samlet P&L",           lambda m: money2(m["pnl"])),
        ("Profit factor",        lambda m: pf_str(m["pf"])),
        ("Gns. vinder",          lambda m: money2(m["avg_win"])),
        ("Gns. taber",           lambda m: money2(m["avg_loss"])),
        ("Payoff-ratio",         lambda m: f"{m['payoff']:.2f}"),
        ("Forventning/handel",   lambda m: money2(m["expect"])),
        ("Største vinder",       lambda m: money2(m["best_trade"])),
        ("Største taber",        lambda m: money2(m["worst_trade"])),
        ("Max drawdown ($)",     lambda m: money2(m["dd_abs"])),
        ("Max drawdown (%)",     lambda m: pct(m["dd_pct"])),
        ("Gns. holdetid",        lambda m: f"{m['avg_hold']:.0f} min"),
        ("Bedste dag",           lambda m: money2(m["best_day"])),
        ("Værste dag",           lambda m: money2(m["worst_day"])),
        ("Sharpe (daglig, ann.)",lambda m: f"{m['sharpe']:.2f}"),
    ]
    data = [["Nøgletal"] + STRATS + ["Samlet"]]
    for label, fn in rows_def:
        data.append([label] + [fn(mets[s]) for s in STRATS] + [fn(comb_met)])
    w0 = 38*mm; ws = (180*mm - w0) / 5
    t1 = Table(data, colWidths=[w0] + [ws]*5, repeatRows=1)
    ts = [
        ("BACKGROUND", (0,0), (-1,0), HEADBG), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 8),
        ("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"), ("ALIGN", (0,0), (0,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 4.2), ("BOTTOMPADDING", (0,0), (-1,-1), 4.2),
        ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("LINEBELOW", (0,0), (-1,0), 0.6, DARK), ("INNERGRID", (0,1), (-1,-1), 0.4, GRID),
        ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (5,1), (5,-1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (5,1), (5,-1), "Helvetica-Bold"),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            ts.append(("BACKGROUND", (0,r), (4,r), ZEBRA))
    pnl_row = 1 + [i for i,(l,_) in enumerate(rows_def) if l == "Samlet P&L"][0]
    for c in range(1, 6):
        neg = "-" in data[pnl_row][c]
        ts.append(("TEXTCOLOR", (c,pnl_row), (c,pnl_row), NEG if neg else POS))
        ts.append(("FONTNAME", (c,pnl_row), (c,pnl_row), "Helvetica-Bold"))
    t1.setStyle(TableStyle(ts))
    story.append(t1)
    story.append(Paragraph("Payoff-ratio = gns. vinder / |gns. taber|. Forventning/handel = samlet "
                           f"P&L / antal handler. Max drawdown (%) er målt mod en notional på "
                           f"${notional:,.0f} pr. strategi. Reconcile-bogføringer (phantom) er udeladt.", NOTE))

    # ── Tabel 2: exit-årsager (dynamiske rækker) ──
    story.append(Spacer(1, 10))
    present = [cat for cat in CAT_ORDER
              if any(len(trades[s]) and (trades[s]["reason"] == cat).any() for s in STRATS)]
    exit_block = [Paragraph("Exit-årsager (antal handler)", H2)]
    d2 = [["Exit-årsag"] + STRATS]
    for cat in present:
        d2.append([cat] + [str(int((trades[s]["reason"] == cat).sum()) if len(trades[s]) else 0)
                           for s in STRATS])
    if len(d2) == 1:
        d2.append(["(ingen handler i perioden)"] + ["0"]*len(STRATS))
    t2 = Table(d2, colWidths=[38*mm] + [(180*mm-38*mm)/4]*4, repeatRows=1)
    t2s = [
        ("BACKGROUND", (0,0), (-1,0), HEADBG), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"),
        ("ALIGN", (1,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("INNERGRID", (0,1), (-1,-1), 0.4, GRID), ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#cbd5e1"))]
    for r in range(1, len(d2)):
        if r % 2 == 0:
            t2s.append(("BACKGROUND", (0,r), (-1,r), ZEBRA))
    t2.setStyle(TableStyle(t2s))
    exit_block.append(t2)
    story.append(KeepTogether(exit_block))

    # ── Tabel 3: P&L pr. måned ──
    story.append(Spacer(1, 10))
    month_block = [Paragraph("P&amp;L pr. måned (USD)", H2)]
    months_in = sorted({(d.year, d.month) for d in days})
    d3 = [["Måned"] + STRATS + ["Samlet"]]
    for (yr, mo) in months_in:
        row = [f"{DK_MONTHS[mo-1].capitalize()} {yr}"]; msum = 0.0
        for s in STRATS:
            df = trades[s]
            v = df[df["day"].apply(lambda d: d.year == yr and d.month == mo)]["pnl"].sum() if len(df) else 0.0
            msum += v; row.append(money2(v))
        row.append(money2(msum)); d3.append(row)
    t3 = Table(d3, colWidths=[30*mm] + [(180*mm-30*mm)/5]*5, repeatRows=1)
    t3s = [
        ("BACKGROUND", (0,0), (-1,0), HEADBG), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("INNERGRID", (0,1), (-1,-1), 0.4, GRID), ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (5,1), (5,-1), colors.HexColor("#f1f5f9")), ("FONTNAME", (5,1), (5,-1), "Helvetica-Bold")]
    for r in range(1, len(d3)):
        if r % 2 == 0:
            t3s.append(("BACKGROUND", (0,r), (4,r), ZEBRA))
        for c in range(1, 6):
            if "-" in d3[r][c]:
                t3s.append(("TEXTCOLOR", (c,r), (c,r), NEG))
    t3.setStyle(TableStyle(t3s))
    month_block.append(t3)
    story.append(KeepTogether(month_block))

    story.append(Spacer(1, 14))
    story.append(Paragraph("Rapporten er bygget på faktiske, lukkede handler fra journalen "
                           "(trading_dash.db) for det valgte interval, filtreret på exit-dato.", NOTE))

    SimpleDocTemplate(str(out_pdf), pagesize=A4, leftMargin=15*mm, rightMargin=15*mm,
                      topMargin=14*mm, bottomMargin=14*mm, title="Strategirapport").build(story)


def _account_label(explicit):
    if explicit:
        return explicit
    try:
        from accounts import load_identity
        idn = load_identity()
        return f"{idn.ibkr_account} ({'paper' if idn.paper_trading else 'LIVE'})"
    except Exception:
        return "—"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Strategi-sammenligningsrapport (PDF) på faktiske handler")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (default: 120 dage før i dag)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: i dag)")
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--out", default=None, help="sti til PDF (default: strategirapport_output/...)")
    ap.add_argument("--account", default=None, help="konto-label (default: fra identity)")
    ap.add_argument("--notional", type=float, default=10_000.0, help="notional pr. strategi (til drawdown-pct)")
    a = ap.parse_args()

    end = date.fromisoformat(a.end) if a.end else date.today()
    start = date.fromisoformat(a.start) if a.start else (end - timedelta(days=120))
    if start > end:
        print("FEJL: --start er efter --end"); return 1

    if not Path(a.db).exists():
        print(f"FEJL: finder ikke databasen: {a.db}"); return 1

    out_dir = (Path(a.out).parent if a.out else (Path.cwd() / "strategirapport_output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = Path(a.out) if a.out else (out_dir / f"strategirapport_{start}_{end}.pdf")
    account = _account_label(a.account)

    days = business_days(start, end)
    trades = load_trades(a.db, start, end)
    dailies, cums, mets = {}, {}, {}
    for s in STRATS:
        daily, cum = daily_curve(trades[s], days)
        dailies[s], cums[s] = daily, cum
        mets[s] = metrics(trades[s], daily, cum, a.notional)

    all_trades = pd.concat([t for t in trades.values() if len(t)], ignore_index=True) \
        if any(len(t) for t in trades.values()) else pd.DataFrame(columns=["day","pnl","hold","win","reason"])
    comb_daily = sum(dailies[s] for s in STRATS)
    comb_cum = comb_daily.cumsum()
    comb_met = metrics(all_trades, comb_daily, comb_cum, a.notional * len(STRATS))

    p_eq, p_bar = build_figures(days, cums, comb_cum, mets, comb_met, out_dir)
    build_pdf(out_pdf, p_eq, p_bar, days, start, end, account, trades, mets, comb_met, a.notional)

    print(f"OK -> {out_pdf}")
    print(f"Periode {start}..{end}  ({len(days)} handelsdage) · konto {account}")
    for s in STRATS:
        m = mets[s]
        print(f"  {s:<16} {m['trades']:>3} handler  WR {m['wr']:>4.0f}%  "
              f"P&L ${m['pnl']:>+9.2f}  PF {pf_str(m['pf'])}")
    print(f"  {'SAMLET':<16} {comb_met['trades']:>3} handler  WR {comb_met['wr']:>4.0f}%  "
          f"P&L ${comb_met['pnl']:>+9.2f}  PF {pf_str(comb_met['pf'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
