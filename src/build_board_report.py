"""Build the monthly board report as a PDF from the reporting extracts (the same CSV files the Qlik dashboards read).

Pages: 1 Summary, 2 Commentary, 3 P&L budget vs actual, 4 Variance drivers, 5 Balance sheet, 6 Credit risk (scorecard;
only if the risk extracts exist), then Notes and definitions. The commentary is written from the numbers; an optional
management comment (automation/commentary/YYYY-MM.md) is shown above it when the file exists. Every number comes from the extracts, so the PDF and the dashboards agree.

Run:  python src/build_board_report.py                (latest month in the data)
      python src/build_board_report.py --month 2025-12
"""
from __future__ import annotations

import argparse
import re
import textwrap
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

from export_for_qlik import WATERFALL_LINES

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "dashboards" / "qlik" / "data"
COMPANY = "Kestrel Credit Ltd"
COMMENTARY_DIR = ROOT / "automation" / "commentary"
COMMENT_MAX = 700          # characters of management comment shown on the page

GREEN, RED, AMBER, BLUE, GREY, INK, MUTE, LIGHT, BG, CARD = (
    "#2da44e", "#d1242f", "#bf8700", "#1f6feb", "#9aa5b1", "#1f2328", "#57606a", "#e6e9ed", "#ffffff", "#f6f8fa")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": LIGHT, "axes.spines.top": False,
                     "axes.spines.right": False, "pdf.fonttype": 42, "text.parse_math": False})
PAGE = (11.69, 8.27)      # A4 landscape, inches
SUBTOTALS = {"Total Revenue", "Net Revenue", "Pre-tax Profit", "Net Income"}
RISK_FILES = ["risk_years.csv", "risk_bands.csv", "risk_feature_psi.csv", "risk_score_deciles.csv", "risk_models.csv"]


# ------------------------------------------------------------------ formatting
def usd(v: float) -> str:
    return f"{'-' if v < 0 else ''}${abs(v):,.0f}"


def usd_m(v: float) -> str:
    return f"{'-' if v < 0 else ''}${abs(v) / 1e6:,.2f}M"


def signed(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"


def pct(v: float, signed_: bool = False) -> str:
    return f"{v:+.1%}" if signed_ else f"{v:.1%}"


def fav(v: float) -> str:
    return "favourable" if v >= 0 else "unfavourable"


def month_label(m: str) -> str:
    return datetime.strptime(m, "%Y-%m").strftime("%B %Y")


# ------------------------------------------------------------------ data
def load_extracts(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    ex = {n: pd.read_csv(data_dir / f"{n}.csv") for n in ("bva_lines", "variance_drivers", "balance_sheet")}
    if all((data_dir / f).exists() for f in RISK_FILES):
        for f in RISK_FILES:
            ex[f[:-4]] = pd.read_csv(data_dir / f)
    return ex


def latest_month(ex: dict) -> str:
    return str(ex["bva_lines"]["period"].max())


def _safe_div(a: float, b: float) -> float:
    return a / b if b else float("nan")


def line_table(ex: dict, month: str) -> tuple[pd.DataFrame, int]:
    """P&L lines for the month and year to date: actual, budget, variance (favourable positive)."""
    lines = ex["bva_lines"]
    cur = lines[lines["period"] == month]
    if cur.empty:
        raise ValueError(f"No data for month {month}. Available: {lines['period'].min()} to {lines['period'].max()}")
    fy = int(cur["fiscal_year"].iloc[0])
    ytd = lines[(lines["fiscal_year"] == fy) & (lines["period"] <= month)]
    key = ["line_order", "line_name"]
    m = cur.set_index(key)[["actual", "budget", "variance"]].add_prefix("m_")
    y = ytd.groupby(key)[["actual", "budget", "variance"]].sum().add_prefix("y_")
    t = m.join(y).reset_index().sort_values("line_order")
    t["m_pct"] = [_safe_div(v, b) for v, b in zip(t.m_variance, t.m_budget)]
    t["y_pct"] = [_safe_div(v, b) for v, b in zip(t.y_variance, t.y_budget)]
    return t.reset_index(drop=True), fy


def board_facts(ex: dict, month: str) -> dict:
    t, fy = line_table(ex, month)
    row = lambda name: t[t.line_name == name].iloc[0]
    ni, rev = row("Net Income"), row("Total Revenue")
    contrib = t[t.line_name.isin(WATERFALL_LINES)].set_index("line_name")["y_variance"]
    f = {"month": month, "label": month_label(month), "fy": fy, "table": t,
         "ni": {"actual": ni.m_actual, "budget": ni.m_budget, "variance": ni.m_variance, "pct": ni.m_pct},
         "ni_ytd": {"actual": ni.y_actual, "budget": ni.y_budget, "variance": ni.y_variance, "pct": ni.y_pct},
         "rev_ytd": {"actual": rev.y_actual, "budget": rev.y_budget, "variance": rev.y_variance, "pct": rev.y_pct},
         "worst": (contrib.idxmin(), float(contrib.min())), "best": (contrib.idxmax(), float(contrib.max())),
         "months_ytd": int((ex["bva_lines"].query("fiscal_year == @fy and period <= @month")["period"].nunique()))}

    bs = ex["balance_sheet"].set_index("period")
    cur = bs.loc[month]
    periods = list(bs.index)
    prev = bs.loc[periods[periods.index(month) - 1]] if periods.index(month) > 0 else cur
    f["bs"] = {"cur": cur, "prev": prev, "total_assets": float(cur.total_assets), "equity": float(cur.total_equity),
               "de": float(cur.debt_to_equity), "coverage": float(cur.allowance_coverage),
               "check": float(cur.balance_check), "assets_change": float(cur.total_assets - prev.total_assets)}

    d = ex["variance_drivers"]
    d = d[(d["period"] <= month) & (d["period"].str[:4] == str(fy)) & d["volume_driver"].notna()]
    g = d.groupby("account_name")[["variance", "volume_effect", "rate_effect"]].sum()
    if len(g):
        top = g["variance"].abs().idxmax()
        f["driver"] = {"account": top, **{k: float(g.loc[top, k]) for k in ("variance", "volume_effect", "rate_effect")}}

    if "risk_years" in ex:
        f["risk"] = risk_facts(ex)
    return f


def risk_facts(ex: dict) -> dict:
    y, b = ex["risk_years"], ex["risk_bands"]
    test = y[y["data"] == "test"].iloc[0]
    tb = b[b["issue_year"] == test["issue_year"]].set_index("band")
    rate = tb["defaults"] / tb["loans"]
    return {"year": int(test["issue_year"]), "gini": float(test["gini"]), "auc": float(test["auc"]), "ks": float(test["ks"]),
            "psi": float(test["score_psi"]), "psi_status": str(test["psi_status"]),
            "actual": float(tb["defaults"].sum() / tb["loans"].sum()), "predicted": float(tb["pd_sum"].sum() / tb["loans"].sum()),
            "rate_a": float(rate.loc["A"]), "rate_e": float(rate.loc["E"]),
            "train_years": sorted(int(v) for v in y.loc[y["data"] == "train", "issue_year"])}


def headlines(f: dict) -> list[str]:
    ni, ytd = f["ni"], f["ni_ytd"]
    out = [f"Net income for {f['label']} was {usd(ni['actual'])} against a budget of {usd(ni['budget'])}: "
           f"{signed(ni['variance'])} ({pct(ni['pct'], True)}), {fav(ni['variance'])}.",
           f"Year to date ({f['months_ytd']} months of FY{f['fy']}) net income is {usd(ytd['actual'])} against a budget of "
           f"{usd(ytd['budget'])}: {signed(ytd['variance'])} ({pct(ytd['pct'], True)}), {fav(ytd['variance'])}.",
           f"Year to date, the largest unfavourable item is {f['worst'][0]} ({signed(f['worst'][1])}) and the largest favourable "
           f"item is {f['best'][0]} ({signed(f['best'][1])})."]
    if "driver" in f:
        d = f["driver"]
        out.append(f"{d['account']} is the account with the biggest driver-based variance: {signed(d['variance'])} year to date, "
                   f"of which {signed(d['rate_effect'])} from rate and {signed(d['volume_effect'])} from volume.")
    b = f["bs"]
    out.append(f"Total assets at month-end were {usd(b['total_assets'])} ({signed(b['assets_change'])} on the prior month). "
               f"Equity was {usd(b['equity'])}, debt-to-equity {b['de']:.2f}x and allowance coverage {pct(b['coverage'])}. "
               f"Assets equal liabilities plus equity (difference {b['check']:,.2f}).")
    if "risk" in f:
        r = f["risk"]
        out.append(f"Credit scorecard, {r['year']} test year: Gini {r['gini']:.3f}, AUC {r['auc']:.3f}, score PSI {r['psi']:.3f} "
                   f"({r['psi_status']}). Default rate rises from {pct(r['rate_a'])} in band A to {pct(r['rate_e'])} in band E; "
                   f"actual {pct(r['actual'])} against predicted {pct(r['predicted'])}.")
    return out


def management_comment(month: str, folder: Path = COMMENTARY_DIR) -> str | None:
    """Text of automation/commentary/<month>.md (headings and comment lines starting # or <!-- removed), or None."""
    path = Path(folder) / f"{month}.md"
    if not path.exists():
        return None
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8-sig").splitlines()]
    text = " ".join(ln for ln in lines if ln and not ln.startswith(("#", "<!--")))
    text = re.sub(r"\s+", " ", re.sub(r"<!--.*?-->", "", text)).strip()
    if not text:
        return None
    if len(text) > COMMENT_MAX:
        text = text[:COMMENT_MAX].rsplit(" ", 1)[0].rstrip(",;:") + " ..."
    return text


def commentary(f: dict) -> dict[str, list[str]]:
    """Short interpretation of the numbers. Only states what the numbers show: no causes outside the data."""
    ni, ytd, rev, b, t = f["ni"], f["ni_ytd"], f["rev_ytd"], f["bs"], f["table"]
    says: list[str] = []
    contrib = t[t.line_name.isin(WATERFALL_LINES)]
    n_bad = int((contrib.y_variance < 0).sum())
    says.append(f"Year to date, net income is {signed(ytd['variance'])} ({pct(ytd['pct'], True)}) against budget, {fav(ytd['variance'])}. "
                f"Of the {len(contrib)} lines that build net income, {n_bad} are unfavourable and {len(contrib) - n_bad} favourable.")
    key, kv = f["worst"] if ytd["variance"] < 0 else f["best"]
    if ytd["variance"] and kv * ytd["variance"] > 0:
        share = kv / ytd["variance"]
        says.append(f"{key} ({signed(kv)}) is {share:.0%} of the year-to-date net income variance; "
                    f"all other lines together net to {signed(ytd['variance'] - kv)}.")
    if ytd["variance"] < 0 and abs(rev["pct"]) < 0.02:
        says.append(f"Revenue is within 2% of budget ({pct(rev['pct'], True)}), so the net income gap comes from costs and credit losses, "
                    f"not from lower revenue.")
    elif rev["pct"]:
        says.append(f"Revenue is {signed(rev['variance'])} ({pct(rev['pct'], True)}) against budget year to date.")
    if "driver" in f:
        d = f["driver"]
        main = "rate" if abs(d["rate_effect"]) >= abs(d["volume_effect"]) else "volume"
        says.append(f"For {d['account']} the variance is mainly a {main} effect: {signed(d['rate_effect'])} from rate and "
                    f"{signed(d['volume_effect'])} from volume.")
    says.append(f"Total assets moved {signed(b['assets_change'])} in the month to {usd(b['total_assets'])}; debt-to-equity is "
                f"{b['de']:.2f}x and the balance sheet balances.")
    if "risk" in f:
        r = f["risk"]
        gap = r["actual"] - r["predicted"]
        if abs(gap) < 0.01:
            cal = f"Actual and predicted default rates agree within one point ({pct(r['actual'])} vs {pct(r['predicted'])})."
        else:
            way, eff = ("under-predicts", "too low") if gap > 0 else ("over-predicts", "too high")
            cal = (f"The model {way} the {r['year']} default rate ({pct(r['actual'])} actual vs {pct(r['predicted'])} predicted); "
                   f"losses estimated from the predicted rate would be {eff}.")
        says.append(f"Credit scorecard: it separates good from bad borrowers (Gini {r['gini']:.3f}; default rate "
                    f"{pct(r['rate_a'])} in band A against {pct(r['rate_e'])} in band E). {cal}")
    says = says[:6]

    watch: list[str] = []
    if ytd["variance"] < 0 and f["worst"][1] < 0:
        watch.append(f"{f['worst'][0]} ({signed(f['worst'][1])} year to date) is the largest unfavourable item: check whether the "
                     f"budget assumption or the underlying trend needs to change.")
    if "risk" in f:
        r = f["risk"]
        gap = r["actual"] - r["predicted"]
        if abs(gap) >= 0.01:
            watch.append(f"Scorecard calibration gap of {gap:+.1%}: recalibrate the predicted default rate before it is used for provisioning.")
        if r["psi"] >= 0.10:
            watch.append(f"Score PSI is {r['psi']:.3f} ({r['psi_status']}): the borrower population has shifted; review the model.")
    chk = f.get("checks")
    if chk is None:
        watch.append("Data checks were not run for this report: re-run the refresh with the database before relying on the figures.")
    elif chk["failed"]:
        watch.append(f"{chk['failed']} data check(s) failed at the last refresh: investigate before circulating this report.")
    prev_de = float(b["prev"].debt_to_equity)
    if abs(b["de"] - prev_de) >= 0.01:
        watch.append(f"Debt-to-equity moved from {prev_de:.2f}x to {b['de']:.2f}x in the month: keep an eye on borrowing against equity.")
    if not watch:
        watch.append("No items need escalation this month: results are within budget and the checks passed.")
    return {"says": says, "watch": watch[:4]}


def metric_definitions(path: Path, names: list[str]) -> list[tuple[str, str, str]]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"\| M\d+ ", line):
                c = [x.strip() for x in line.strip().strip("|").split("|")]
                if c[1] in names:
                    out[c[1]] = (c[0], c[1], c[2])
    return [out[n] for n in names if n in out]


def check_summary(path: Path | None) -> dict | None:
    if not path or not Path(path).exists():
        return None
    s = pd.read_csv(path)["status"].value_counts()
    return {"passed": int(s.get("PASS", 0)), "failed": int(s.get("FAIL", 0)), "skipped": int(s.get("SKIPPED", 0)),
            "total": int(s.sum())}


# ------------------------------------------------------------------ drawing helpers
class Doc:
    def __init__(self, pdf: PdfPages, label: str, total: int, generated: str):
        self.pdf, self.label, self.total, self.generated, self.n = pdf, label, total, generated, 0

    def page(self, title: str, subtitle: str = ""):
        self.n += 1
        fig = plt.figure(figsize=PAGE, facecolor=BG)
        fig.text(0.04, 0.945, title, fontsize=19, weight="bold", color=INK, va="center")
        if subtitle:
            fig.text(0.04, 0.905, subtitle, fontsize=9.5, color=MUTE, va="center")
        fig.add_artist(plt.Line2D([0.04, 0.96], [0.885, 0.885], color=LIGHT, transform=fig.transFigure))
        fig.add_artist(plt.Line2D([0.04, 0.96], [0.05, 0.05], color=LIGHT, transform=fig.transFigure))
        fig.text(0.04, 0.03, f"{COMPANY}  |  Monthly board report  |  {self.label}", fontsize=8, color=MUTE, va="center")
        fig.text(0.96, 0.03, f"Page {self.n} of {self.total}  |  Generated {self.generated}", fontsize=8, color=MUTE,
                 va="center", ha="right")
        return fig

    def save(self, fig):
        self.pdf.savefig(fig)
        plt.close(fig)


def card(fig, x, y, w, h, title=None, sub=None):
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.006", transform=fig.transFigure,
                                      facecolor=CARD, edgecolor=LIGHT, zorder=-5))
    if title:
        fig.text(x + 0.012, y + h - 0.016, title, fontsize=10.5, weight="bold", color=INK, va="top")
    if sub:
        fig.text(x + 0.012, y + h - 0.040, sub, fontsize=8, color=MUTE, va="top")


def axes(fig, rect):
    ax = fig.add_axes(rect)
    ax.set_zorder(5)
    ax.patch.set_alpha(0)
    return ax


def kpi(fig, x, y, w, h, title, value, sub, colour=INK):
    card(fig, x, y, w, h)
    fig.text(x + 0.012, y + h - 0.016, title, fontsize=8.5, color=MUTE, va="top")
    fig.text(x + 0.012, y + h * 0.56, value, fontsize=16, weight="bold", color=colour, va="center")
    fig.text(x + 0.012, y + 0.024, sub, fontsize=7.5, color=colour if colour != INK else MUTE, va="center", linespacing=1.5)


def table(fig, top, left, cols, rows, row_h=0.0285, header=None):
    """cols: list of (x, align); rows: list of (cells, style) with style = dict(bold, colours)."""
    y = top
    if header:
        for (x, al), h in zip(cols, header):
            fig.text(x, y, h, fontsize=8.5, weight="bold", color=MUTE, ha=al, va="center")
        y -= row_h * 0.7
        fig.add_artist(plt.Line2D([left, cols[-1][0] + 0.005], [y, y], color=LIGHT, transform=fig.transFigure))
        y -= row_h * 0.6
    for cells, st in rows:
        for i, ((x, al), c) in enumerate(zip(cols, cells)):
            fig.text(x, y, c, fontsize=8.5, ha=al, va="center", weight="bold" if st.get("bold") else "normal",
                     color=st.get("colours", {}).get(i, INK))
        y -= row_h
    return y


def colour(v: float) -> str:
    return GREEN if v >= 0 else RED


def kfmt(ax, axis="y"):
    f = lambda v, p: f"{v / 1e6:.1f}M" if abs(v) >= 1e6 else f"{v / 1e3:,.0f}k"
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


# ------------------------------------------------------------------ pages
def page_summary(doc: Doc, f: dict):
    fig = doc.page("Summary", f"{COMPANY}, {f['label']}. Year to date = January to {f['label'].split()[0]} of FY{f['fy']}. USD.")
    ni, ytd, rev, b = f["ni"], f["ni_ytd"], f["rev_ytd"], f["bs"]
    w, h, y = 0.178, 0.12, 0.735
    xs = [0.04 + i * 0.1855 for i in range(5)]
    kpi(fig, xs[0], y, w, h, f"Net income, {f['label']}", usd(ni["actual"]),
        f"Budget {usd(ni['budget'])}\n{signed(ni['variance'])} ({pct(ni['pct'], True)})", colour(ni["variance"]))
    kpi(fig, xs[1], y, w, h, "Net income, year to date", usd(ytd["actual"]),
        f"Budget {usd(ytd['budget'])}\n{signed(ytd['variance'])} ({pct(ytd['pct'], True)})", colour(ytd["variance"]))
    kpi(fig, xs[2], y, w, h, "Total revenue, year to date", usd(rev["actual"]),
        f"Budget {usd(rev['budget'])}\n{signed(rev['variance'])} ({pct(rev['pct'], True)})", colour(rev["variance"]))
    kpi(fig, xs[3], y, w, h, "Total assets, month-end", usd(b["total_assets"]), f"{signed(b['assets_change'])} on prior month")
    kpi(fig, xs[4], y, w, h, "Debt-to-equity", f"{b['de']:.2f}x", f"Allowance coverage {pct(b['coverage'])}")
    card(fig, 0.04, 0.09, 0.92, 0.615, "Headlines")
    yy = 0.645
    for line in headlines(f):
        wrapped = textwrap.wrap(line, 118)
        fig.text(0.058, yy, "•", fontsize=11, color=BLUE, va="top")
        for k, part in enumerate(wrapped):
            fig.text(0.07, yy - k * 0.026, part, fontsize=10, color=INK, va="top")
        yy -= 0.026 * len(wrapped) + 0.03
    doc.save(fig)


STYLES = [dict(width=128, size=10.5, line=0.025, gap=0.02), dict(width=140, size=9.5, line=0.022, gap=0.012)]   # normal, compact


def _bullets(fig, x, y, items, width, size, line, gap) -> float:
    """Draw bullet paragraphs from y downwards; return the y after the last one."""
    for item in items:
        parts = textwrap.wrap(item, width)
        fig.text(x, y, "•", fontsize=size + 1, color=BLUE, va="top")
        for k, part in enumerate(parts):
            fig.text(x + 0.013, y - k * line, part, fontsize=size, color=INK, va="top")
        y -= line * len(parts) + gap
    return y


def _bullet_height(items, width, size, line, gap) -> float:
    return sum(line * len(textwrap.wrap(i, width)) + gap for i in items)


def page_commentary(doc: Doc, f: dict, comment: str | None):
    fig = doc.page("Commentary", f"What the numbers say and what to watch. Written from the figures in this report. {f['label']}.")
    c = commentary(f)
    head, gap, avail = 0.05, 0.016, 0.865 - 0.07
    for st in STYLES:                                   # use the compact style only if the text does not fit
        cw = textwrap.wrap(comment, st["width"] - 8) if comment else []
        heights = ([head + st["line"] * len(cw) + 0.02] if comment else []) + \
                  [head + _bullet_height(c[k], **st) + 0.004 for k in ("says", "watch")]
        if sum(heights) + gap * (len(heights) - 1) <= avail:
            break
    blocks = ([("Management comment", "Added by the finance team for this month", cw)] if comment else []) + \
             [("What the numbers say", None, c["says"]), ("Watch next month", None, c["watch"])]
    top = 0.865
    for (title, sub, items), h in zip(blocks, heights):
        card(fig, 0.04, top - h, 0.92, h, title, sub)
        if title == "Management comment":
            for k, part in enumerate(items):
                fig.text(0.058, top - head - 0.012 - k * st["line"], part, fontsize=st["size"], color=INK, va="top", style="italic")
        else:
            _bullets(fig, 0.058, top - head - 0.012, items, **st)
        top -= h + gap
    doc.save(fig)


def page_pl(doc: Doc, f: dict, ex: dict):
    fig = doc.page("Profit and loss: actual vs budget", "Variance: favourable is positive for revenue and cost lines alike (M30). USD.")
    t = f["table"]
    cols = [(0.05, "left"), (0.29, "right"), (0.37, "right"), (0.45, "right"), (0.51, "right"),
            (0.62, "right"), (0.70, "right"), (0.79, "right"), (0.85, "right")]
    fig.text(0.29, 0.862, f["label"], fontsize=9, weight="bold", color=INK, ha="left")
    fig.text(0.62, 0.862, f"Year to date (FY{f['fy']})", fontsize=9, weight="bold", color=INK, ha="left")
    rows = []
    for _, r in t.iterrows():
        cells = [r.line_name, f"{r.m_actual:,.0f}", f"{r.m_budget:,.0f}", f"{r.m_variance:+,.0f}",
                 "n/a" if np.isnan(r.m_pct) else f"{r.m_pct:+.1%}", f"{r.y_actual:,.0f}", f"{r.y_budget:,.0f}",
                 f"{r.y_variance:+,.0f}", "n/a" if np.isnan(r.y_pct) else f"{r.y_pct:+.1%}"]
        rows.append((cells, {"bold": r.line_name in SUBTOTALS,
                             "colours": {3: colour(r.m_variance), 4: colour(r.m_variance), 7: colour(r.y_variance), 8: colour(r.y_variance)}}))
    y = table(fig, 0.835, 0.05, cols, rows, header=["P&L line", "Actual", "Budget", "Variance", "Var %", "Actual", "Budget", "Variance", "Var %"])
    card(fig, 0.04, 0.065, 0.92, y - 0.075 if y > 0.30 else 0.22, "Net income by month: actual vs budget")
    ax = axes(fig, [0.08, 0.10, 0.85, max(y - 0.20, 0.10)])
    lines = ex["bva_lines"]
    m = lines[(lines.line_name == "Net Income") & (lines.fiscal_year == f["fy"]) & (lines.period <= f["month"])].sort_values("period")
    x = np.arange(len(m))
    ax.bar(x - 0.2, m.actual, 0.38, color=BLUE, label="Actual")
    ax.bar(x + 0.2, m.budget, 0.38, color=AMBER, label="Budget")
    ax.set_xticks(x)
    ax.set_xticklabels(pd.to_datetime(m.month_end).dt.strftime("%b"))
    kfmt(ax)
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=2, loc="upper left", fontsize=8)
    doc.save(fig)


def page_drivers(doc: Doc, f: dict, ex: dict):
    fig = doc.page("Variance drivers (year to date)", "Left: budget net income to actual net income. Right: volume and rate effects (M32, M33).")
    card(fig, 0.04, 0.07, 0.47, 0.79, "Budget to actual net income", f"Year to date, FY{f['fy']}")
    ax = axes(fig, [0.07, 0.13, 0.42, 0.62])
    t = f["table"].set_index("line_name")
    run = float(t.loc["Net Income", "y_budget"])
    ax.bar(0, run, color=GREY, width=0.65)
    ax.text(0, run, usd_m(run), ha="center", va="bottom", fontsize=8)
    for i, name in enumerate(WATERFALL_LINES, 1):
        v = float(t.loc[name, "y_variance"])
        ax.bar(i, abs(v), bottom=run if v >= 0 else run + v, color=colour(v), width=0.65)
        ax.text(i, max(run, run + v), f"{v / 1e3:+,.0f}k", ha="center", va="bottom", fontsize=7.5)
        run += v
    end = float(t.loc["Net Income", "y_actual"])
    ax.bar(len(WATERFALL_LINES) + 1, end, color=BLUE, width=0.65)
    ax.text(len(WATERFALL_LINES) + 1, end, usd_m(end), ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(WATERFALL_LINES) + 2))
    ax.set_xticklabels(["Budget"] + [textwrap.fill(n, 11) for n in WATERFALL_LINES] + ["Actual"], fontsize=7)
    kfmt(ax)
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)

    card(fig, 0.53, 0.07, 0.43, 0.79, "Volume, rate and other effects", "Accounts with a volume driver show volume and rate; the rest show other")
    ax = axes(fig, [0.70, 0.13, 0.25, 0.62])
    d = ex["variance_drivers"]
    d = d[(d.period <= f["month"]) & (d.period.str[:4] == str(f["fy"]))]
    g = d.groupby("account_name")[["volume_effect", "rate_effect", "other_effect"]].sum().fillna(0)
    g = g[g.abs().sum(axis=1) > 0.5]
    g["tot"] = g.sum(axis=1)
    g = g.sort_values("tot")
    pos, neg = np.zeros(len(g)), np.zeros(len(g))
    for col, c, nm in (("volume_effect", BLUE, "Volume"), ("rate_effect", AMBER, "Rate"), ("other_effect", GREY, "Other")):
        v = g[col].to_numpy()
        ax.barh(range(len(g)), v, left=np.where(v >= 0, pos, neg), color=c, height=0.6, label=nm)
        pos += np.where(v >= 0, v, 0)
        neg += np.where(v < 0, v, 0)
    ax.set_yticks(range(len(g)))
    ax.set_yticklabels(g.index, fontsize=7.5)
    ax.axvline(0, color=INK, lw=0.8)
    kfmt(ax, "x")
    ax.grid(axis="x", color=LIGHT)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=7.5, loc="lower right")
    doc.save(fig)


def page_balance(doc: Doc, f: dict, ex: dict):
    fig = doc.page("Balance sheet", f"Month-end {f['label']}. USD.")
    b = f["bs"]
    cur, prev = b["cur"], b["prev"]
    bs = ex["balance_sheet"].set_index("period")
    py = bs[bs["fiscal_year"] == f["fy"] - 1].tail(1)
    py = py.iloc[0] if len(py) else prev
    items = [("Cash", "cash"), ("Gross loans", "gross_loans"), ("Allowance for credit losses", "allowance"), ("Net loans", "net_loans"),
             ("Total assets", "total_assets"), ("Borrowings", "borrowings"), ("Total liabilities", "total_liabilities"),
             ("Total equity", "total_equity")]
    cols = [(0.05, "left"), (0.26, "right"), (0.35, "right"), (0.43, "right")]
    rows = [([n, f"{cur[k]:,.0f}", f"{prev[k]:,.0f}", f"{cur[k] - prev[k]:+,.0f}"],
             {"bold": k in ("total_assets", "total_liabilities", "total_equity")}) for n, k in items]
    rows.append((["Debt-to-equity", f"{cur.debt_to_equity:.2f}x", f"{prev.debt_to_equity:.2f}x", f"{cur.debt_to_equity - prev.debt_to_equity:+.2f}"], {}))
    rows.append((["Allowance coverage", pct(cur.allowance_coverage), pct(prev.allowance_coverage),
                  f"{(cur.allowance_coverage - prev.allowance_coverage) * 100:+.2f} pts"], {}))
    card(fig, 0.04, 0.07, 0.46, 0.79, "Month-end position", f"vs prior month-end ({month_label(prev.name)})")
    y = table(fig, 0.775, 0.05, cols, rows, header=["", "Month-end", "Prior month", "Change"], row_h=0.034)
    ok = abs(b["check"]) < 0.005
    fig.text(0.05, y - 0.012, f"Check: total assets = liabilities + equity. Difference {b['check']:,.2f}  ({'OK' if ok else 'DOES NOT BALANCE'}).",
             fontsize=8.5, color=GREEN if ok else RED, va="center")
    fig.text(0.05, y - 0.045, f"Year-end {py.name}: total assets {py.total_assets:,.0f}, equity {py.total_equity:,.0f}, "
             f"debt-to-equity {py.debt_to_equity:.2f}x.", fontsize=8, color=MUTE, va="center")

    hist = bs.loc[:f["month"]].tail(13)
    card(fig, 0.52, 0.45, 0.44, 0.41, "Total assets, borrowings and equity")
    ax = axes(fig, [0.56, 0.49, 0.38, 0.28])
    x = np.arange(len(hist))
    ax.bar(x - 0.27, hist.total_assets, 0.26, color=GREY, label="Total assets")
    ax.bar(x, hist.borrowings, 0.26, color=AMBER, label="Borrowings")
    ax.bar(x + 0.27, hist.total_equity, 0.26, color=BLUE, label="Equity")
    ax.set_xticks(x)
    ax.set_xticklabels([p[2:] for p in hist.index], fontsize=7, rotation=45)
    kfmt(ax)
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=3, fontsize=7.5, loc="upper left")
    card(fig, 0.52, 0.07, 0.44, 0.36, "Debt-to-equity")
    ax = axes(fig, [0.56, 0.11, 0.38, 0.25])
    ax.plot(x, hist.debt_to_equity, color=BLUE, marker="o", lw=2)
    ax.set_xticks(x)
    ax.set_xticklabels([p[2:] for p in hist.index], fontsize=7, rotation=45)
    ax.yaxis.set_major_formatter(lambda v, p: f"{v:.1f}x")
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)
    doc.save(fig)


def page_risk(doc: Doc, f: dict, ex: dict):
    r = f["risk"]
    fig = doc.page("Credit risk: scorecard", f"Lending Club 36-month loans issued {min(r['train_years'])}-{r['year']}. Train {', '.join(map(str, r['train_years']))}, "
                   f"test {r['year']}. Separate from the ledger on pages 1 to 4 and not reconciled to it.")
    psi_c = GREEN if r["psi"] < 0.10 else AMBER if r["psi"] < 0.25 else RED
    gap = r["actual"] - r["predicted"]
    xs = [0.04 + i * 0.1855 for i in range(5)]
    kpi(fig, xs[0], 0.735, 0.178, 0.12, f"Gini, {r['year']} test", f"{r['gini']:.3f}", "Higher is better (M53)")
    kpi(fig, xs[1], 0.735, 0.178, 0.12, f"AUC, {r['year']} test", f"{r['auc']:.3f}", "0.5 = no skill (M52)")
    kpi(fig, xs[2], 0.735, 0.178, 0.12, f"KS, {r['year']} test", f"{r['ks']:.3f}", "Higher is better (M54)")
    kpi(fig, xs[3], 0.735, 0.178, 0.12, "Score PSI, test vs train", f"{r['psi']:.3f}", r["psi_status"], psi_c)
    kpi(fig, xs[4], 0.735, 0.178, 0.12, "Calibration gap", f"{gap:+.1%}", f"Actual {pct(r['actual'])}\nvs predicted {pct(r['predicted'])}",
        RED if abs(gap) >= 0.01 else GREEN)
    b, y = ex["risk_bands"], ex["risk_years"]
    card(fig, 0.04, 0.07, 0.46, 0.64, "Default rate by risk band", "A = safest, E = riskiest")
    ax = axes(fig, [0.07, 0.11, 0.41, 0.50])
    years = sorted(b["issue_year"].unique())
    shades = ["#c8d1da", "#8aa4c0", BLUE, "#0b3d91"][-len(years):] if len(years) <= 4 else [BLUE] * len(years)
    wd = 0.8 / len(years)
    for j, yr in enumerate(years):
        g = b[b.issue_year == yr].set_index("band")
        ax.bar(np.arange(5) + (j - (len(years) - 1) / 2) * wd, (g["defaults"] / g["loans"]).reindex(list("ABCDE")).to_numpy() * 100,
               wd * 0.92, color=shades[j], label=str(yr))
    ax.set_xticks(range(5))
    ax.set_xticklabels(list("ABCDE"))
    ax.yaxis.set_major_formatter(lambda v, p: f"{v:.0f}%")
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=len(years), loc="upper left", fontsize=8, title="Issue year", title_fontsize=8)
    card(fig, 0.52, 0.07, 0.44, 0.64, "Actual vs predicted default rate by issue year", "Ranking holds; the level can drift")
    ax = axes(fig, [0.56, 0.11, 0.38, 0.50])
    ax.plot(y.issue_year, y.default_rate * 100, color=RED, marker="o", lw=2, label="Actual")
    ax.plot(y.issue_year, y.avg_predicted_pd * 100, color=BLUE, marker="o", lw=2, label="Predicted")
    ax.set_xticks(list(y.issue_year))
    ax.yaxis.set_major_formatter(lambda v, p: f"{v:.0f}%")
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left", fontsize=8)
    doc.save(fig)


def page_notes(doc: Doc, f: dict, dictionary: Path, checks: dict | None):
    fig = doc.page("Notes and definitions")
    names = ["Net Income", "Total Revenue", "Variance", "Variance %", "Volume Effect", "Rate Effect", "Debt-to-Equity", "Allowance Coverage"]
    if "risk" in f:
        names += ["AUC", "Gini", "KS statistic", "Population Stability Index (PSI)"]
    notes = ["Sources: synthetic double-entry ledger of Kestrel Credit Ltd (fictional lender), budget and drivers; PostgreSQL views "
             "v_bva_summary, v_variance_drivers and v_balance_sheet_monthly. Same extracts feed the Qlik Sense dashboards.",
             "Sign convention: favourable variance is positive. Revenue lines: actual minus budget. Cost lines: budget minus actual.",
             "Year to date covers the fiscal year of the report month, from January to the report month."]
    if "risk" in f:
        notes.append("Credit risk uses Lending Club loans (public data), a different dataset from the ledger. It is not reconciled to the ledger figures.")
    if checks:
        notes.append(f"Data checks at the last refresh: {checks['passed']} of {checks['total']} passed, {checks['failed']} failed"
                     + (f", {checks['skipped']} skipped" if checks["skipped"] else "") + ".")
    else:
        notes.append("Data checks: no check results were available when this report was built.")
    y = 0.83
    for n in notes:
        parts = textwrap.wrap(n, 150)
        fig.text(0.05, y, "•", fontsize=11, color=BLUE, va="top")
        for k, p in enumerate(parts):
            fig.text(0.065, y - k * 0.024, p, fontsize=9, va="top")
        y -= 0.024 * len(parts) + 0.018
    fig.text(0.05, y - 0.01, "Metric definitions (docs/metric_dictionary.md)", fontsize=11, weight="bold", va="top")
    y -= 0.05
    for mid, name, d in metric_definitions(dictionary, names):
        fig.text(0.05, y, f"{mid}", fontsize=8.5, weight="bold", color=BLUE, va="top")
        fig.text(0.095, y, name, fontsize=8.5, weight="bold", va="top")
        parts = textwrap.wrap(d, 110)
        for k, p in enumerate(parts):
            fig.text(0.29, y - k * 0.021, p, fontsize=8.5, va="top")
        y -= 0.021 * len(parts) + 0.012
    doc.save(fig)


# ------------------------------------------------------------------ main
def build_report(month: str | None = None, data_dir: Path = DATA_DIR, out_path: Path | None = None,
                 checks_csv: Path | None = None, dictionary: Path = ROOT / "docs" / "metric_dictionary.md",
                 generated: str | None = None, commentary_dir: Path = COMMENTARY_DIR) -> tuple[Path, dict]:
    ex = load_extracts(data_dir)
    month = month or latest_month(ex)
    f = board_facts(ex, month)
    out_path = Path(out_path or ROOT / "reports" / f"board_report_{month}.pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 7 if "risk" in f else 6
    comment = management_comment(month, commentary_dir)
    f["has_comment"] = comment is not None
    generated = generated or datetime.now().strftime("%d %b %Y %H:%M")
    checks = check_summary(checks_csv)
    f["checks"], f["pages"], f["pdf"] = checks, total, str(out_path)
    with PdfPages(out_path, metadata={"Title": f"{COMPANY} board report {f['label']}", "Author": "DA-04 reporting system",
                                      "Subject": "Monthly board report", "Keywords": f"board report, {month}"}) as pdf:
        doc = Doc(pdf, f["label"], total, generated)
        page_summary(doc, f)
        page_commentary(doc, f, comment)
        page_pl(doc, f, ex)
        page_drivers(doc, f, ex)
        page_balance(doc, f, ex)
        if "risk" in f:
            page_risk(doc, f, ex)
        page_notes(doc, f, dictionary, checks)
    return out_path, f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--month", help="YYYY-MM (default: latest month in the data)")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--commentary-dir", type=Path, default=COMMENTARY_DIR)
    ap.add_argument("--checks-csv", type=Path, default=ROOT / "reports" / "data_checks.csv")
    a = ap.parse_args()
    path, f = build_report(a.month, a.data_dir, a.out, a.checks_csv, commentary_dir=a.commentary_dir)
    print(f"Board report for {f['label']}: {path} ({f['pages']} pages)")


if __name__ == "__main__":
    main()
