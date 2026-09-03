"""
Red/green flag engine — the deterministic notification layer.

Reads ONLY the curated exports in reports/ (the same files Power BI
reads) and emits reports/client_fs_flags.csv: one row per finding, in
three colors:

    RED    — act now: a threshold is breached or a decision says stop
    YELLOW — review: fragile, borderline, or an open control question
    GREEN  — confirmed healthy: the check ran and passed

Every threshold here mirrors the verdict cards on tab 2 (Current
Position) and the three-tests logic on tab 5 (Options) — one rulebook,
two surfaces. The engine never invents numbers: each flag quotes the
figures it was computed from and names the tab + file it came from.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPORTS = Path(__file__).resolve().parents[2] / "reports"

FLAG_COLUMNS = [
    "flag_id", "color", "area", "headline", "detail",
    "recommended_action", "source_tab", "source_file", "value_class",
]

# color sort used by the export and the report table
_COLOR_ORDER = {"RED": 0, "YELLOW": 1, "GREEN": 2}


def _statement_aggregates(reports_dir: Path) -> dict:
    """Mirror the tab-2 DAX exactly: latest period, REPORTED rows only."""
    df = pd.read_csv(reports_dir / "client_fs_statements.csv")
    latest = df["period_id"].max()
    d = df[(df["period_id"] == latest)
           & (df["reported_or_adjusted"] == "REPORTED")]

    def section(name):
        return d.loc[d["statement_section"] == name, "amount_reporting"].sum()

    def account(name):
        return d.loc[d["standard_account_id"] == name,
                     "amount_reporting"].sum()

    ca = section("current_assets")
    cl = section("current_liabilities")
    is_rows = d[d["statement_type"] == "IS"]
    ebit = is_rows.loc[~is_rows["standard_account_id"].isin(
        ["interest_expense", "income_tax_expense"]), "amount_reporting"].sum()
    return {
        "period": latest,
        "current_assets": ca,
        "current_liabilities": cl,
        "cash": account("cash"),
        "inventory": account("inventory"),
        "equity": section("equity"),
        "debt": account("long_term_debt"),
        "ebit": ebit,
        "interest": -account("interest_expense"),
        "revenue": account("revenue"),
        "cogs": -account("cogs"),
        "da": -account("depreciation_amortization")
              if account("depreciation_amortization") < 0
              else account("depreciation_amortization"),
    }


def _latest_actual_ufcf(reports_dir: Path) -> pd.Series:
    df = pd.read_csv(reports_dir / "client_fs_ufcf.csv")
    actual = df[(df["forecast_method"] == "ACTUAL")
                & (df["scenario"].str.upper() == "BASE")]
    return actual.sort_values("period_id").iloc[-1]


def build_flags(reports_dir: Path = REPORTS) -> pd.DataFrame:
    """Compute every flag from the curated exports. Deterministic."""
    s = _statement_aggregates(reports_dir)
    u = _latest_actual_ufcf(reports_dir)
    base = pd.read_csv(reports_dir / "finance_scenario_report.csv")
    base = base[base["scenario"] == "Base"].iloc[0]

    rows: list[dict] = []

    def flag(color, area, headline, detail, action, tab, file):
        rows.append({
            "flag_id": f"FLAG-{len(rows) + 1:03d}", "color": color,
            "area": area, "headline": headline, "detail": detail,
            "recommended_action": action, "source_tab": tab,
            "source_file": file, "value_class": "MODEL_OUTPUT",
        })

    # ---------------- liquidity (tab 2 thresholds) ----------------
    stmts = "client_fs_statements.csv"
    cur = s["current_assets"] / s["current_liabilities"]
    if cur < 1.0:
        flag("RED", "Liquidity", f"Current ratio {cur:.2f}x below 1.0x",
             "Bills come due faster than assets turn to cash.",
             "Line up financing or accelerate collections now.",
             "2 · Current Position", stmts)
    elif cur > 3.0:
        flag("YELLOW", "Liquidity",
             f"Current ratio {cur:.2f}x above the 3.0x band",
             "Strong cover, but capital this idle may be earning nothing.",
             "Review whether excess cash should fund options on tab 5.",
             "2 · Current Position", stmts)
    else:
        flag("GREEN", "Liquidity", f"Current ratio {cur:.2f}x in band",
             "Inside the healthy 1.5-3.0x range.", "None.",
             "2 · Current Position", stmts)

    quick = (s["current_assets"] - s["inventory"]) / s["current_liabilities"]
    if quick < 1.0:
        flag("RED", "Liquidity", f"Quick ratio {quick:.2f}x below 1.0x",
             "Cannot cover near-term bills without selling inventory.",
             "Tighten AR collection; review inventory turnover.",
             "2 · Current Position", stmts)
    else:
        flag("GREEN", "Liquidity", f"Quick ratio {quick:.2f}x >= 1.0x target",
             "Bills covered without touching inventory.", "None.",
             "2 · Current Position", stmts)

    cashr = s["cash"] / s["current_liabilities"]
    flag("GREEN" if cashr >= 0.2 else "YELLOW", "Liquidity",
         f"Cash ratio {cashr:.2f}x vs 0.2x floor",
         "Immediate cover from cash alone." if cashr >= 0.2
         else "Thin immediate cover from cash alone.",
         "None." if cashr >= 0.2 else "Watch the daily cash position.",
         "2 · Current Position", stmts)

    # ---------------- solvency ----------------
    de = s["debt"] / s["equity"]
    if de > 2.0:
        flag("RED", "Solvency", f"Debt/equity {de:.2f}x above 2.0x",
             "Lenders own the downside at this gearing.",
             "Prioritize the debt-paydown option on tab 5.",
             "2 · Current Position", stmts)
    else:
        flag("GREEN", "Solvency", f"Debt/equity {de:.2f}x conservative",
             "At or below the 1.0x conservative line."
             if de <= 1.0 else "Geared but inside the 2.0x ceiling.",
             "None.", "2 · Current Position", stmts)

    cov = s["ebit"] / s["interest"]
    if cov < 1.5:
        flag("RED", "Solvency", f"Interest coverage {cov:.1f}x below 1.5x",
             "Operating profit barely covers the interest bill.",
             "Restructure debt before any new investment.",
             "2 · Current Position", stmts)
    elif cov < 3.0:
        flag("YELLOW", "Solvency", f"Interest coverage {cov:.1f}x below 3.0x",
             "Cover is positive but thin against a downturn.",
             "Stress-test EBITDA scenarios on tab 3.",
             "2 · Current Position", stmts)
    else:
        flag("GREEN", "Solvency",
             f"Interest coverage {cov:.1f}x comfortable",
             f"EBIT {s['ebit']:.1f} covers interest {s['interest']:.1f} "
             "well above the 3.0x target.", "None.",
             "2 · Current Position", stmts)

    nd_ebitda = base["net_debt"] / u["ebitda"]
    headroom = 2.0 * u["ebitda"] - base["net_debt"]
    if nd_ebitda > 2.0:
        flag("RED", "Solvency",
             f"Net debt/EBITDA {nd_ebitda:.2f}x over the 2.0x policy",
             "The borrowing ceiling is breached.",
             "Pay down to policy before funding new options.",
             "2 · Current Position", "finance_scenario_report.csv")
    else:
        flag("GREEN", "Solvency",
             f"Net debt/EBITDA {nd_ebitda:.2f}x within the 2.0x policy",
             f"Debt headroom ${headroom:.1f}M before the ceiling.",
             "Headroom can fund tab-5 options without breaching policy.",
             "2 · Current Position", "finance_scenario_report.csv")

    # ---------------- returns & cash generation ----------------
    fsr = "finance_scenario_report.csv"
    spread = base["roic_wacc_spread_pct"]
    flag("GREEN" if spread > 0 else "RED", "Returns",
         f"ROIC beats WACC by {spread:.1f}pts" if spread > 0
         else f"ROIC below WACC by {-spread:.1f}pts",
         f"ROIC {base['roic_pct']:.1f}% vs WACC {base['wacc_pct']:.2f}% — "
         + ("each invested dollar creates value." if spread > 0
            else "each invested dollar destroys value."),
         "None." if spread > 0 else "Halt expansion; fix the core first.",
         "2 · Current Position", fsr)

    ufcf = u["ufcf"]
    flag("GREEN" if ufcf >= 0 else "RED", "Cash",
         f"Free cash flow ${ufcf:.1f}M — "
         + ("self-funded" if ufcf >= 0 else "burning cash"),
         "The operation funds its own reinvestment."
         if ufcf >= 0 else "Runway is the countdown.",
         "None." if ufcf >= 0 else "Cut burn or raise capital.",
         "2 · Current Position", "client_fs_ufcf.csv")

    # ---------------- reinvestment quality ----------------
    da_pct = 100.0 * u["da"] / u["revenue"]
    capex_da = u["capex"] / u["da"]
    if da_pct > 8:
        flag("YELLOW", "Reinvestment",
             f"D&A {da_pct:.1f}% of revenue — capital-intensive",
             "CFO review: capex discipline, asset utilization, impairment "
             "risk, and whether depreciation schedules match asset lives.",
             "Walk the fixed-asset register against the capex plan.",
             "2 · Current Position", "client_fs_ufcf.csv")
    elif da_pct < 3:
        flag("YELLOW", "Reinvestment",
             f"D&A {da_pct:.1f}% of revenue — unusually light",
             "Asset-light OR under-invested; CapEx/D&A well below 1.0x "
             "would mean the base is aging.",
             f"Check CapEx/D&A (now {capex_da:.2f}x).",
             "2 · Current Position", "client_fs_ufcf.csv")
    else:
        flag("GREEN", "Reinvestment",
             f"D&A {da_pct:.1f}% of revenue, CapEx/D&A {capex_da:.2f}x",
             "Moderate intensity; the asset base is replenished at least "
             "as fast as it wears." if capex_da >= 1.0 else
             "Moderate intensity, but capex is running below wear.",
             "None." if capex_da >= 1.0 else "Review the capex plan.",
             "2 · Current Position", "client_fs_ufcf.csv")

    # ---------------- options (tab 5 verdicts) ----------------
    projects = pd.read_csv(reports_dir / "client_fs_projects.csv")
    projects = projects[projects["scenario"].str.upper() == "BASE"]
    verdicts = pd.read_csv(reports_dir / "client_fs_option_verdicts.csv")
    decision = verdicts[verdicts["reading"].str.startswith("DECISION")]
    rate_cols = [c for c in decision.columns if c.startswith("at_")]

    for _, p in projects.sort_values("project_id").iterrows():
        color = {"APPROVE": "GREEN", "REVIEW": "YELLOW",
                 "REJECT": "RED"}[p["recommendation"]]
        flag(color, "Options",
             f"{p['project_id']} {p['project_name']}: "
             f"{p['recommendation']}",
             p["recommendation_reason"],
             "Proceed per the recommendation." if color == "GREEN"
             else "Resolve before committing capital.",
             "5 · Options", "client_fs_projects.csv")

        if p["recommendation"] in ("APPROVE", "REVIEW"):
            row = decision[decision["project_id"] == p["project_id"]]
            rejects = [c.replace("at_", "").replace("pct", "%")
                       for c in rate_cols
                       if row.iloc[0][c] == "REJECT"]
            if rejects:
                flag("YELLOW", "Options",
                     f"{p['project_id']} is rate-fragile",
                     f"Flips to REJECT at discount rates "
                     f"{', '.join(rejects)} in the sensitivity strip.",
                     "Confirm the hurdle rate before approving.",
                     "5 · Options", "client_fs_option_verdicts.csv")

    # ---------------- open control findings (analyst agent) ----------
    review = pd.read_csv(reports_dir / "client_fs_review.csv")
    sev_color = {"HIGH": "RED", "MEDIUM": "YELLOW", "LOW": "YELLOW"}
    for _, r in review.sort_values("review_id").iterrows():
        color = sev_color.get(r["severity"], "YELLOW")
        # the agent's own "No action needed" verdict = reviewed & benign
        if str(r["recommended_action"]).startswith("No action needed"):
            color = "GREEN"
        flag(color, "Controls",
             f"{r['review_id']} {r['item_type']} ({r['severity']})",
             r["explanation"], r["recommended_action"],
             "1 · Financials", "client_fs_review.csv")

    out = pd.DataFrame(rows, columns=FLAG_COLUMNS)
    out = out.sort_values(
        by=["color", "area", "flag_id"],
        key=lambda c: c.map(_COLOR_ORDER) if c.name == "color" else c,
    ).reset_index(drop=True)
    out["flag_id"] = [f"FLAG-{i + 1:03d}" for i in range(len(out))]
    return out
