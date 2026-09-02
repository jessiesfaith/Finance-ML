"""
Project appraisal (Page 6): the owner's own investment cases.

The discipline is INCREMENTAL - a project is valued on what it changes
versus the without-project world, never by re-forecasting the whole
company. The walk per year t over the horizon H:

    incremental revenue_t = Y1 x (1 + g)^(t-1)      (and/or cost savings)
    EBITDA_t  = revenue_t x margin + savings_t
    D&A_t     = initial investment / H               (straight line)
    NOPAT_t   = (EBITDA_t - D&A_t) x (1 - tax)
    NWC_t     = nwc%% x revenue_t;  dNWC_t = NWC_t - NWC_{t-1}
    CapEx_t   = maint%% x revenue_t
    UFCF_t    = NOPAT_t + D&A_t - CapEx_t - dNWC_t
    (final year: the NWC balance is recovered - inventory sells down)

Then, per scenario, on the SAME hurdle basis Page 4 uses (DECISIONS on
audit D2): NPV at the hurdle rate, IRR (the rate where NPV = 0),
undiscounted payback, and incremental ROIC = average NOPAT / average
invested capital (initial investment less cumulative D&A, plus NWC).
Three tests - NPV > 0, IRR > hurdle, ROIC > WACC - are read together:
all pass = APPROVE, all fail = REJECT, anything mixed = REVIEW.

Rates (tax, WACC, hurdle per scenario) come from
reports/finance_scenario_report.csv - the same single source of truth
the DCF page reads - so a project and the company are always judged in
the same world.
"""

from pathlib import Path

import pandas as pd

from . import validator
from .loader import ClientFSValidationError, _coerce_types, _read_csv
from .schemas import PROJECT_ASSUMPTIONS, PROJECT_MASTER

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECTS_DIR = BASE_DIR / "data" / "projects"
RATES_FILE = BASE_DIR / "reports" / "finance_scenario_report.csv"
DEFAULT_OUTPUT = BASE_DIR / "reports" / "client_fs_projects.csv"

MAX_HORIZON = 5  # v1: the export carries ufcf_y1..y5

OUTPUT_COLUMNS = [
    "project_id", "project_name", "category", "scenario", "review_status",
    "initial_investment", "horizon_years", "tax_rate_pct", "wacc_pct",
    "hurdle_rate_pct", "ufcf_y1", "ufcf_y2", "ufcf_y3", "ufcf_y4", "ufcf_y5",
    "npv_at_hurdle", "irr_pct", "payback_years", "incr_roic_pct",
    "npv_test", "irr_test", "roic_test", "recommendation", "rationale",
    "value_class",
]


def load_projects(projects_dir=None, strict=True):
    """Fail-loud load of the project intake tables."""
    projects_dir = Path(projects_dir) if projects_dir else PROJECTS_DIR
    issues, tables = [], {}
    for schema in (PROJECT_MASTER, PROJECT_ASSUMPTIONS):
        path = projects_dir / schema.filename
        if not path.exists():
            issues.append(validator.Issue(
                "ERROR", schema.table, "missing_file",
                f"required file not found: {path}"))
            continue
        raw = _read_csv(path)
        issues.extend(validator.validate_table(raw, schema))
        tables[schema.table] = _coerce_types(raw, schema)

    if "project_master" in tables and "project_assumptions" in tables:
        master = tables["project_master"]
        assumptions = tables["project_assumptions"]
        unknown = set(assumptions["project_id"]) - set(master["project_id"])
        if unknown:
            issues.append(validator.Issue(
                "ERROR", "project_assumptions", "unknown_project",
                f"assumption rows reference unknown project(s): {sorted(unknown)}"))
        bad_h = master[(master["horizon_years"] < 1)
                       | (master["horizon_years"] > MAX_HORIZON)]
        for _, row in bad_h.iterrows():
            issues.append(validator.Issue(
                "ERROR", "project_master", "horizon_out_of_range",
                f"{row['project_id']}: horizon_years must be 1..{MAX_HORIZON}"))
        bad_i = master[master["initial_investment"] <= 0]
        for _, row in bad_i.iterrows():
            issues.append(validator.Issue(
                "ERROR", "project_master", "nonpositive_investment",
                f"{row['project_id']}: initial_investment must be > 0"))

    errors = [i for i in issues if i.severity == "ERROR"]
    if strict and errors:
        raise ClientFSValidationError(errors)
    return tables, issues


def load_rates(path=None) -> pd.DataFrame:
    """Per-scenario tax / WACC / hurdle from the valuation report CSV."""
    path = Path(path) if path else RATES_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run `python models/export_finance_report.py` "
            "first; projects are judged with the same rates as the company DCF")
    frame = pd.read_csv(path)
    needed = {"scenario", "scenario_sort", "tax_rate_pct", "wacc_pct",
              "hurdle_rate_pct", "cost_of_debt_pct"}
    missing = needed - set(frame.columns)
    if missing:
        raise ValueError(f"{path} lacks required columns: {sorted(missing)}")
    return (frame[sorted(needed)].drop_duplicates("scenario")
            .sort_values("scenario_sort").reset_index(drop=True))


def _value(assumptions, project_id, code, default=0.0):
    rows = assumptions[(assumptions["project_id"] == project_id)
                       & (assumptions["assumption_code"] == code)]
    return float(rows["value"].iloc[0]) if len(rows) else default


def _irr(investment, flows, lo=-0.95, hi=10.0, tol=1e-9):
    """Bisection: the rate where NPV crosses zero, or None if it never does."""
    def npv(rate):
        return -investment + sum(
            f / (1 + rate) ** t for t, f in enumerate(flows, start=1))
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def _appraise_debt_paydown(project, rates_row) -> dict:
    """
    Paying down debt as a competing use of cash. Economically it is
    buying back your own bond at par: each year saves the after-tax
    coupon on the amount retired, and at the horizon the borrowing
    capacity is restored - so its IRR IS the after-tax cost of debt,
    exactly. Judged on the same three tests for comparability, it will
    read REJECT at the equity hurdle: right arithmetic, wrong ruler for
    a risk-free use of cash. The page says so - the paydown return is
    the FLOOR every risky project must beat, and the option is weighed
    on safety and headroom, not hurdle math.
    """
    pid = project["project_id"]
    horizon = int(project["horizon_years"])
    investment = float(project["initial_investment"])
    tax = float(rates_row["tax_rate_pct"]) / 100.0
    wacc = float(rates_row["wacc_pct"])
    hurdle = float(rates_row["hurdle_rate_pct"])
    kd_after_tax = float(rates_row["cost_of_debt_pct"]) / 100.0 * (1 - tax)

    saved = investment * kd_after_tax        # after-tax interest avoided
    flows = [saved] * horizon
    flows[-1] += investment                  # capacity restored at horizon

    npv = -investment + sum(
        f / (1 + hurdle / 100.0) ** t for t, f in enumerate(flows, start=1))
    irr = _irr(investment, flows)
    roic = kd_after_tax * 100.0              # saved NOPAT / capital retired

    payback = None
    cumulative = 0.0
    for t, f in enumerate(flows, start=1):
        if cumulative + f >= investment and f > 0:
            payback = round(t - 1 + (investment - cumulative) / f, 2)
            break
        cumulative += f

    npv_test = "PASS" if npv > 0 else "FAIL"
    irr_test = "PASS" if irr is not None and irr * 100 > hurdle else "FAIL"
    roic_test = "PASS" if roic > wacc else "FAIL"
    tests = (npv_test, irr_test, roic_test)
    recommendation = ("APPROVE" if tests == ("PASS",) * 3 else
                      "REJECT" if tests == ("FAIL",) * 3 else "REVIEW")

    row = {
        "project_id": pid,
        "project_name": project["project_name"],
        "category": project["category"],
        "scenario": rates_row["scenario"],
        "review_status": project["review_status"],
        "initial_investment": round(investment, 4),
        "horizon_years": horizon,
        "tax_rate_pct": round(tax * 100, 4),
        "wacc_pct": round(wacc, 4),
        "hurdle_rate_pct": round(hurdle, 4),
        "npv_at_hurdle": round(npv, 4),
        "irr_pct": round(irr * 100, 4) if irr is not None else None,
        "payback_years": payback,
        "incr_roic_pct": round(roic, 4),
        "npv_test": npv_test,
        "irr_test": irr_test,
        "roic_test": roic_test,
        "recommendation": recommendation,
        "rationale": project["rationale"],
        "value_class": "CALCULATED",
    }
    for t in range(1, MAX_HORIZON + 1):
        row[f"ufcf_y{t}"] = round(flows[t - 1], 4) if t <= horizon else None
    return row


def appraise_project(project, assumptions, rates_row) -> dict:
    pid = project["project_id"]
    horizon = int(project["horizon_years"])
    investment = float(project["initial_investment"])
    tax = float(rates_row["tax_rate_pct"]) / 100.0
    wacc = float(rates_row["wacc_pct"])
    hurdle = float(rates_row["hurdle_rate_pct"])

    if str(project["category"]).upper() == "DEBT_PAYDOWN":
        return _appraise_debt_paydown(project, rates_row)

    rev1 = _value(assumptions, pid, "INCR_REVENUE_Y1")
    rev_g = _value(assumptions, pid, "INCR_REVENUE_GROWTH_PCT")
    margin = _value(assumptions, pid, "INCR_EBITDA_MARGIN_PCT")
    sav1 = _value(assumptions, pid, "COST_SAVINGS_Y1")
    sav_g = _value(assumptions, pid, "COST_SAVINGS_GROWTH_PCT")
    capex_pct = _value(assumptions, pid, "MAINT_CAPEX_PCT_REV")
    nwc_pct = _value(assumptions, pid, "NWC_PCT_REV")

    da = investment / horizon
    flows, nopats, capitals = [], [], []
    nwc_prev = 0.0
    for t in range(1, horizon + 1):
        revenue = rev1 * (1 + rev_g / 100.0) ** (t - 1)
        savings = sav1 * (1 + sav_g / 100.0) ** (t - 1)
        ebitda = revenue * margin / 100.0 + savings
        nopat = (ebitda - da) * (1 - tax)
        nwc = nwc_pct / 100.0 * revenue
        delta_nwc = nwc - nwc_prev
        capex = capex_pct / 100.0 * revenue
        ufcf = nopat + da - capex - delta_nwc
        if t == horizon:
            ufcf += nwc                     # working capital recovered
        # start-of-year invested capital: what's still tied up
        capitals.append(investment - da * (t - 1) + nwc_prev)
        nopats.append(nopat)
        flows.append(ufcf)
        nwc_prev = nwc

    npv = -investment + sum(
        f / (1 + hurdle / 100.0) ** t for t, f in enumerate(flows, start=1))
    irr = _irr(investment, flows)
    avg_ic = sum(capitals) / len(capitals)
    roic = (sum(nopats) / len(nopats)) / avg_ic * 100.0 if avg_ic else None

    payback = None
    cumulative = 0.0
    for t, f in enumerate(flows, start=1):
        if cumulative + f >= investment and f > 0:
            payback = round(t - 1 + (investment - cumulative) / f, 2)
            break
        cumulative += f

    npv_test = "PASS" if npv > 0 else "FAIL"
    irr_test = "PASS" if irr is not None and irr * 100 > hurdle else "FAIL"
    roic_test = "PASS" if roic is not None and roic > wacc else "FAIL"
    tests = (npv_test, irr_test, roic_test)
    recommendation = ("APPROVE" if tests == ("PASS",) * 3 else
                      "REJECT" if tests == ("FAIL",) * 3 else "REVIEW")

    row = {
        "project_id": pid,
        "project_name": project["project_name"],
        "category": project["category"],
        "scenario": rates_row["scenario"],
        "review_status": project["review_status"],
        "initial_investment": round(investment, 4),
        "horizon_years": horizon,
        "tax_rate_pct": round(tax * 100, 4),
        "wacc_pct": round(wacc, 4),
        "hurdle_rate_pct": round(hurdle, 4),
        "npv_at_hurdle": round(npv, 4),
        "irr_pct": round(irr * 100, 4) if irr is not None else None,
        "payback_years": payback,
        "incr_roic_pct": round(roic, 4) if roic is not None else None,
        "npv_test": npv_test,
        "irr_test": irr_test,
        "roic_test": roic_test,
        "recommendation": recommendation,
        "rationale": project["rationale"],
        "value_class": "CALCULATED",
    }
    for t in range(1, MAX_HORIZON + 1):
        row[f"ufcf_y{t}"] = round(flows[t - 1], 4) if t <= horizon else None
    return row


def build_project_appraisal(master, assumptions, rates) -> pd.DataFrame:
    rows = []
    for _, project in master.sort_values("project_id").iterrows():
        for _, rates_row in rates.iterrows():
            rows.append(appraise_project(project, assumptions, rates_row))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


# ----------------------------------------------------------------------
# Option sensitivity (Step 5): the classic deal table - discount rates
# across the columns, delivery of the planned cash flows down the rows,
# NPV in the cells, and an explicit APPROVE / REJECT strip underneath.
# ----------------------------------------------------------------------

SENSITIVITY_RATES = (5.0, 7.0, 9.0, 11.0, 13.0)
FLOWS_DELIVERED_PCT = (80, 90, 100, 110, 120)
SENSITIVITY_OUTPUT = BASE_DIR / "reports" / "client_fs_option_sensitivity.csv"
VERDICT_STRIP_OUTPUT = BASE_DIR / "reports" / "client_fs_option_verdicts.csv"


def rate_column(rate: float) -> str:
    return "npv_at_" + f"{rate:.0f}pct"


def _base_flows(project, assumptions, base_rates_row):
    """The option's Base-scenario annual flows, via the appraisal engine."""
    row = appraise_project(project, assumptions, base_rates_row)
    horizon = int(project["horizon_years"])
    return [row[f"ufcf_y{t}"] for t in range(1, horizon + 1)]


def build_option_sensitivity(master, assumptions, rates):
    """
    Returns (grid, verdicts). Grid rows: project x flows-delivered %,
    columns: NPV at each discount rate. A DEBT_PAYDOWN's flows are
    contractual, so its rows do not scale with delivery %. Verdicts:
    one row per project - APPROVE where NPV > 0 at that column's rate,
    judged at 100% delivery.
    """
    base = rates[rates["scenario"] == "Base"].iloc[0]
    grid_rows, verdict_rows = [], []
    for _, project in master.sort_values("project_id").iterrows():
        investment = float(project["initial_investment"])
        contractual = str(project["category"]).upper() == "DEBT_PAYDOWN"
        flows = _base_flows(project, assumptions, base)

        for pct in FLOWS_DELIVERED_PCT:
            scale = 1.0 if contractual else pct / 100.0
            row = {"project_id": project["project_id"],
                   "flows_delivered_pct": pct}
            for rate in SENSITIVITY_RATES:
                npv = -investment + sum(
                    (f * scale) / (1 + rate / 100.0) ** t
                    for t, f in enumerate(flows, start=1))
                row[rate_column(rate)] = round(npv, 2)
            grid_rows.append(row)

        verdict = {"project_id": project["project_id"],
                   "reading": "DECISION at planned flows (NPV > 0?)"}
        for rate in SENSITIVITY_RATES:
            npv = -investment + sum(
                f / (1 + rate / 100.0) ** t
                for t, f in enumerate(flows, start=1))
            verdict["at_" + f"{rate:.0f}pct"] = (
                "APPROVE" if npv > 0 else "REJECT")
        verdict_rows.append(verdict)

    grid_cols = (["project_id", "flows_delivered_pct"]
                 + [rate_column(r) for r in SENSITIVITY_RATES])
    verdict_cols = (["project_id", "reading"]
                    + ["at_" + f"{r:.0f}pct" for r in SENSITIVITY_RATES])
    return (pd.DataFrame(grid_rows, columns=grid_cols),
            pd.DataFrame(verdict_rows, columns=verdict_cols))
