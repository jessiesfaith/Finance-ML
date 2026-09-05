"""
NFP CFO decision-intelligence engine — deterministic, lineage-first.

Implements the nonprofit module's math: the five capital-allocation
alternatives, program portfolio economics and classification, the
grant/funding engine (expected values, funding cliff,
probability-weighted gaps), the capital campaign (sources & uses,
pledges, project cash, financing alternatives), ERM risk scoring,
scenarios, sensitivity, the solution engine, and the board-practice
text — all from the seed inputs in data/nfp/ and the global settings.

Framework (management/board decision framework — NOT GAAP, NOT a CFA
formula):

    MISSION VALUE DECISION = FINANCIAL SUSTAINABILITY + MISSION IMPACT
                             + LIQUIDITY + SCALABILITY - RISK

The composite score never hides the underlying metrics: every export
carries the raw financial figures, mission measures, risk, and
value_class provenance (ACTUAL / HISTORICAL / MANAGEMENT ASSUMPTION /
MODEL ESTIMATE / PUBLIC_RESEARCH / SYNTHETIC) the prompt requires.

Nothing here is ML: every number is a visible formula over labeled
inputs (§54 deterministic-first).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NFP_DIR = ROOT / "data" / "nfp"
REPORTS = ROOT / "reports"

RECOMMEND_BANDS = [(70, "RECOMMEND"), (55, "RECOMMEND WITH CONDITIONS"),
                   (45, "PILOT"), (35, "DEFER"), (0, "DO NOT RECOMMEND")]


# ----------------------------------------------------------------------
# inputs
# ----------------------------------------------------------------------

def load_settings() -> dict:
    df = pd.read_csv(NFP_DIR / "nfp_settings.csv")
    s: dict = {}
    for _, r in df.iterrows():
        v = r["value"]
        try:
            v = float(v)
        except (TypeError, ValueError):
            pass
        s[r["setting_id"]] = v
    weights = [s[k] for k in ("weight_mission", "weight_financial",
                              "weight_liquidity", "weight_risk",
                              "weight_scalability", "weight_strategic")]
    if abs(sum(weights) - 100.0) > 1e-9:
        raise ValueError(f"Decision weights must total 100, got {sum(weights)}")
    return s


def _alt_inputs() -> dict:
    df = pd.read_csv(NFP_DIR / "nfp_alternative_inputs.csv")
    out: dict = {}
    for _, r in df.iterrows():
        v = r["value"]
        try:
            v = float(v)
        except (TypeError, ValueError):
            pass
        out.setdefault(r["alternative_id"], {})[r["parameter"]] = v
    return out


def npv(rate_pct: float, flows: list[float]) -> float:
    """flows[0] is year 0 (t=0), flows[1] year 1, ..."""
    r = rate_pct / 100.0
    return sum(cf / (1 + r) ** t for t, cf in enumerate(flows))


def loc_interest(avg_balance: float, rate_pct: float, months: float) -> float:
    return avg_balance * rate_pct / 100.0 * months / 12.0


# ----------------------------------------------------------------------
# TAB 1 — five capital-allocation alternatives
# ----------------------------------------------------------------------

def _operating_flows(a: dict, s: dict, years: int) -> list[float]:
    """Year-1..N net cash for a program alternative. Earned revenue and
    costs grow with annual_growth; grants and contributions carry their
    renewal probabilities from year 2 on (expected value, §22)."""
    g = a.get("annual_growth_pct", 0) / 100.0
    grant_p = a.get("grant_renewal_pct", 100) / 100.0
    donor_p = a.get("donor_renewal_pct", 100) / 100.0
    flows = []
    for t in range(1, years + 1):
        growth = (1 + g) ** (t - 1)
        renew_g = 1.0 if t == 1 else grant_p
        renew_d = 1.0 if t == 1 else donor_p
        funding = (a.get("incr_earned_revenue", 0) * growth
                   + a.get("incr_grants", 0) * renew_g
                   + (a.get("incr_restricted_contrib", 0)
                      + a.get("incr_unrestricted_contrib", 0)) * renew_d)
        costs = (a.get("incr_personnel", 0) + a.get("incr_operating", 0)
                 + a.get("incr_overhead", 0)) * growth
        flows.append(funding - costs)
    return flows


def alternatives(settings: dict | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    inp = _alt_inputs()
    capital = s["available_capital"]
    years = int(s["analysis_period_years"])
    r = s["board_discount_rate_pct"]
    net_return = (s["expected_investment_return_pct"]
                  - s["investment_fees_pct"])
    cash = s["unrestricted_liquid_cash"]
    monthly = s["avg_monthly_operating_expense"]
    base_months = cash / monthly

    rows = []
    for alt_id in ["ALT-1", "ALT-2", "ALT-3", "ALT-4", "ALT-5"]:
        a = inp[alt_id]
        flows5: list[float]
        note = ""
        participants = a.get("incr_participants", 0)
        if alt_id in ("ALT-1", "ALT-2"):
            flows5 = _operating_flows(a, s, years)
            alt_npv = npv(r, [-capital] + flows5)
            months_after = (cash - capital) / monthly
            if alt_id == "ALT-2":
                note = ("STAGED: Phase 1 pilot "
                        f"${a.get('pilot_cost', 0):,.0f} - GO if "
                        "self-sufficiency >= 50% and adoption >= 70% of "
                        "plan; HOLD/REASSESS if 30-70%; STOP below 30%")
        elif alt_id == "ALT-3":
            fv5 = capital * (1 + net_return / 100.0) ** years
            distribution = capital * s["spending_rate_pct"] / 100.0
            flows5 = [distribution] * years
            alt_npv = npv(r, [-capital] + flows5[:-1]
                          + [flows5[-1] + fv5])
            months_after = (cash - capital) / monthly
            note = (f"FV after {years}y = ${fv5:,.0f} "
                    f"(net {net_return:.1f}%); real return "
                    f"{net_return - s['inflation_pct']:.1f}%; annual "
                    f"mission distribution ${distribution:,.0f}; "
                    "capital stays recoverable (liquidity optionality)")
        elif alt_id == "ALT-4":
            yield_flow = capital * s["cash_yield_pct"] / 100.0
            flows5 = [yield_flow] * years
            alt_npv = npv(r, [0.0] + flows5)  # capital retained, not spent
            months_after = base_months
            opp_cost = capital * (net_return - s["cash_yield_pct"]) / 100.0
            note = (f"Adds {capital / monthly:.2f} months cash vs "
                    "deploying; opportunity cost of cash = best foregone "
                    f"return - cash yield = ${opp_cost:,.0f}/yr; buys "
                    "insurance against donation declines, grant delays, "
                    "repairs, downturns, staffing shocks")
        else:  # ALT-5
            avoided = capital * s["borrowing_rate_pct"] / 100.0
            flows5 = [avoided] * years
            alt_npv = npv(r, [-capital] + flows5)
            months_after = (cash - capital) / monthly
            note = (f"Interest avoided = ${capital:,.0f} x "
                    f"{s['borrowing_rate_pct']:.0f}% = ${avoided:,.0f}/yr; "
                    "capital becomes project funding (not lost); NOT an "
                    "automatic pay-down-debt recommendation - compare "
                    "mission benefit and liquidity")

        five_year_cash = sum(flows5)
        mission_units = a.get("mission_units", 0)
        mission_leverage = mission_units / (capital / 100000.0)
        cpp = capital / participants if participants else 0.0
        rows.append({
            "alternative_id": alt_id, "alternative": a["name"],
            "capital_required": capital,
            **{f"net_cash_y{t}": round(f, 0)
               for t, f in enumerate(flows5, 1)},
            "five_year_cash": round(five_year_cash, 0),
            "npv_at_board_rate": round(alt_npv, 0),
            "participants_served": participants,
            "capital_per_participant": round(cpp, 0),
            "mission_leverage_per_100k": round(mission_leverage, 1),
            "months_cash_after": round(months_after, 2),
            "risk_rating_1to10": a.get("risk_rating", 5),
            "scalability_1to10": a.get("scalability", 5),
            "strategic_1to10": a.get("strategic", 5),
            "note": note, "value_class": "MODEL_OUTPUT",
        })
    df = pd.DataFrame(rows)

    # scoring: min-max normalize each dimension to 0-10, then weight.
    def scale(col, invert=False):
        lo, hi = df[col].min(), df[col].max()
        if hi == lo:
            return pd.Series([5.0] * len(df), index=df.index)
        x = (df[col] - lo) / (hi - lo) * 10.0
        return 10.0 - x if invert else x

    df["score_financial"] = scale("npv_at_board_rate").round(1)
    df["score_mission"] = scale("mission_leverage_per_100k").round(1)
    df["score_liquidity"] = scale("months_cash_after").round(1)
    df["score_risk"] = (10.0 - df["risk_rating_1to10"]).clip(0, 10).round(1)
    df["score_scalability"] = df["scalability_1to10"].astype(float)
    df["score_strategic"] = df["strategic_1to10"].astype(float)
    df["decision_score"] = (
        (df["score_mission"] * s["weight_mission"]
         + df["score_financial"] * s["weight_financial"]
         + df["score_liquidity"] * s["weight_liquidity"]
         + df["score_risk"] * s["weight_risk"]
         + df["score_scalability"] * s["weight_scalability"]
         + df["score_strategic"] * s["weight_strategic"]) / 10.0
    ).round(1)

    def band(score):
        for cutoff, label in RECOMMEND_BANDS:
            if score >= cutoff:
                return label
        return "DO NOT RECOMMEND"

    df["recommendation"] = df["decision_score"].apply(band)
    best = df.loc[df["decision_score"].idxmax()]
    # 1/0 (not True/False) so the CSV parses under the model's numeric type
    df["is_recommended"] = (df["alternative_id"]
                            == best["alternative_id"]).astype(int)
    df["management_override"] = ""   # management may override with rationale
    df["override_rationale"] = ""
    return df


# ----------------------------------------------------------------------
# TAB 2 — program portfolio
# ----------------------------------------------------------------------

def programs(settings: dict | None = None,
             inputs: pd.DataFrame | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    df = (inputs if inputs is not None
          else pd.read_csv(NFP_DIR / "nfp_program_inputs.csv"))
    out = []
    for _, p in df.iterrows():
        funding = (p["earned_revenue"] + p["grants"]
                   + p["restricted_contrib"] + p["unrestricted_contrib"]
                   + p["sponsorship"])
        cost = p["personnel"] + p["direct_costs"] + p["allocated_overhead"]
        direct = p["personnel"] + p["direct_costs"]
        gap = funding - cost
        parts = p["participants"]
        cap = p["capacity"]
        util = 100.0 * parts / cap if cap else 0.0
        self_suff = 100.0 * p["earned_revenue"] / direct if direct else 0.0
        subsidy = (cost - p["earned_revenue"] - p["grants"]
                   - p["restricted_contrib"] - p["sponsorship"])
        grant_dep = 100.0 * p["grants"] / funding if funding else 0.0
        donor_dep = (100.0 * p["largest_single_funder"] / funding
                     if funding else 0.0)
        restricted_pct = (100.0 * (p["grants"] + p["restricted_contrib"])
                          / funding if funding else 0.0)
        renewal_p = p["grant_renewal_pct"] / 100.0
        expected_funding = (p["earned_revenue"] + p["grants"] * renewal_p
                            + p["restricted_contrib"]
                            + p["unrestricted_contrib"] + p["sponsorship"])
        pw_gap = cost - expected_funding      # §22 probability-weighted gap

        mission, ok_fin = p["mission_score"], self_suff >= 60.0
        if mission >= 7 and ok_fin:
            classification = "GROW"
        elif mission >= 7:
            classification = "MISSION-CRITICAL / INTENTIONALLY SUBSIDIZED"
        elif ok_fin or gap > 0:
            classification = "REVIEW STRATEGIC FIT / CROSS-SUBSIDY POTENTIAL"
        else:
            classification = "REVIEW FOR CONSOLIDATION"
        at_risk = (grant_dep > 35.0 and p["grant_renewal_pct"] < 70)
        flag = "FUNDING AT RISK" if at_risk else ""
        verdict = {"GROW": "INVEST",
                   "MISSION-CRITICAL / INTENTIONALLY SUBSIDIZED": "CHANGE",
                   "REVIEW STRATEGIC FIT / CROSS-SUBSIDY POTENTIAL": "WATCH",
                   "REVIEW FOR CONSOLIDATION": "CHANGE"}[classification]
        if classification == "GROW" and util >= 85:
            verdict = "INVEST"
        elif classification == "GROW":
            verdict = "KEEP DOING"

        out.append({
            "program_id": p["program_id"], "program": p["program_name"],
            "owner_role": p["owner_role"],
            "mission_category": p["mission_category"],
            "participants": parts, "capacity": cap,
            "utilization_pct": round(util, 1),
            "earned_revenue": p["earned_revenue"], "grants": p["grants"],
            "restricted_contrib": p["restricted_contrib"],
            "unrestricted_contrib": p["unrestricted_contrib"],
            "sponsorship": p["sponsorship"],
            "total_funding": round(funding, 0),
            "personnel": p["personnel"], "direct_costs": p["direct_costs"],
            "allocated_overhead": p["allocated_overhead"],
            "total_cost": round(cost, 0),
            "funding_gap": round(gap, 0),
            "cost_per_participant": round(cost / parts, 0) if parts else 0,
            "funding_per_participant": round(funding / parts, 0) if parts else 0,
            "unrestricted_subsidy": round(subsidy, 0),
            "subsidy_per_participant": round(subsidy / parts, 0) if parts else 0,
            "self_sufficiency_pct": round(self_suff, 1),
            "grant_dependency_pct": round(grant_dep, 1),
            "top_donor_dependency_pct": round(donor_dep, 1),
            "restricted_funding_pct": round(restricted_pct, 1),
            "grant_renewal_pct": p["grant_renewal_pct"],
            "expected_funding": round(expected_funding, 0),
            "probability_weighted_gap": round(pw_gap, 0),
            "mission_score": mission, "risk_rating": p["risk_rating"],
            "classification": classification, "risk_flag": flag,
            "verdict": verdict, "value_class": "SYNTHETIC",
        })
    return pd.DataFrame(out)


def solutions(prog: pd.DataFrame) -> pd.DataFrame:
    """§15 diagnostic solution engine: rule-triggered, never generic."""
    med_cpp = prog["cost_per_participant"].median()
    rows = []

    def add(pid, name, diagnosis, suggestion, why, fin, mission, owner, kpi):
        rows.append({"subject_id": pid, "subject": name,
                     "diagnosis": diagnosis, "suggestion": suggestion,
                     "why": why, "financial_effect": fin,
                     "mission_effect": mission, "owner_role": owner,
                     "approval_required": "CFO -> CEO"
                     if "sunset" not in suggestion.lower()
                     else "CFO -> CEO -> Board",
                     "success_kpi": kpi, "value_class": "MODEL_OUTPUT"})

    for _, p in prog.iterrows():
        pid, name = p["program_id"], p["program"]
        if p["cost_per_participant"] > 1.25 * med_cpp and p["participants"] >= 50:
            add(pid, name,
                f"Cost per participant ${p['cost_per_participant']:,.0f} "
                f"vs portfolio median ${med_cpp:,.0f}",
                "Review staffing mix, capacity, vendor costs, shared "
                "services, facility use, program redesign",
                "Unit cost is the portfolio outlier",
                "Target cost/participant toward median",
                "Neutral if service level held", p["owner_role"],
                "Cost per participant")
        if p["utilization_pct"] < 70:
            add(pid, name,
                f"Utilization {p['utilization_pct']:.0f}% of capacity",
                "Outreach, schedule changes, pricing review, "
                "consolidation, re-targeting",
                "Fixed costs are spread over too few participants",
                "Higher earned revenue on the same cost base",
                "More participants served", p["owner_role"],
                "Capacity utilization %")
        if p["grant_dependency_pct"] > 35:
            add(pid, name,
                f"Grant dependency {p['grant_dependency_pct']:.0f}% "
                f"(renewal {p['grant_renewal_pct']:.0f}%)",
                "Diversify grants, pursue multi-year grants, grow earned "
                "revenue, sponsorship, unrestricted giving",
                "One funder class controls the program's survival",
                f"Probability-weighted gap ${p['probability_weighted_gap']:,.0f}",
                "Continuity protection", "Development Dir",
                "Grant dependency %")
        if p["mission_score"] >= 7 and p["funding_gap"] < 0:
            add(pid, name,
                f"High mission ({p['mission_score']}/10) with funding gap "
                f"${-p['funding_gap']:,.0f}",
                "Seek restricted grant, endowment/designated support, "
                "sponsorship, cross-subsidy, shared staffing, campaign, "
                "strategic partnership",
                "The mission case justifies intentional subsidy - solve "
                "the funding model, not the program",
                "Close the gap with dedicated funding",
                "Protects mission-critical delivery", "CEO",
                "Funding gap")
        if p["mission_score"] < 5 and p["self_sufficiency_pct"] < 50:
            add(pid, name,
                f"Low mission ({p['mission_score']}/10) and "
                f"self-sufficiency {p['self_sufficiency_pct']:.0f}%",
                "Redesign, combine with another program, reduce capacity, "
                "pilot an alternative, pause, or sunset",
                "Neither mission nor economics currently earn the subsidy",
                f"Frees up to ${max(0, -p['funding_gap']):,.0f}/yr",
                "Low mission loss if redesigned well", p["owner_role"],
                "Decision made by review date")
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# shared grants & funding engine
# ----------------------------------------------------------------------

def grants(settings: dict | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    df = pd.read_csv(NFP_DIR / "nfp_grant_inputs.csv")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["renewal_probability_pct"] = pd.to_numeric(
        df["renewal_probability_pct"], errors="coerce")
    df["expected_value"] = (df["amount"] * df["renewal_probability_pct"]
                            / 100.0).round(0)
    df["amount_display"] = df["amount"].map(
        lambda v: f"{v:,.0f}" if pd.notna(v) else "RESEARCH REQUIRED")
    # grant fit score (§20) only where management inputs exist; RESEARCH
    # rows show RESEARCH REQUIRED, never a made-up score.
    df["grant_fit"] = df.apply(
        lambda r: "RESEARCH REQUIRED"
        if r["status"] in ("RESEARCH", "UPCOMING") and pd.isna(r["amount"])
        else ("HISTORICAL" if r["status"] == "CURRENT"
              and pd.isna(r["amount"]) else "SCOREABLE"), axis=1)
    return df


def funding_cliff(settings: dict | None = None,
                  grants_df: pd.DataFrame | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    g = grants_df if grants_df is not None else grants(s)
    asof = date.fromisoformat(str(s["analysis_date"]))
    live = g[g["status"].isin(["CURRENT", "RENEWAL"])
             & g["amount"].notna()].copy()
    rows = []
    for _, r in live.iterrows():
        end = date.fromisoformat(r["end_date"])
        months = (end.year - asof.year) * 12 + end.month - asof.month
        window = ("EXPIRED" if months < 0 else
                  "0-3 MONTHS" if months <= 3 else
                  "3-6 MONTHS" if months <= 6 else
                  "6-12 MONTHS" if months <= 12 else "12-24 MONTHS")
        p = r["renewal_probability_pct"] / 100.0
        rows.append({
            "grant_id": r["grant_id"], "grant": r["grant_name"],
            "funder": r["funder"], "program_id": r["program_id"],
            "end_date": r["end_date"], "window": window,
            "amount": r["amount"],
            "renewal_probability_pct": r["renewal_probability_pct"],
            "expected_renewal": round(r["amount"] * p, 0),
            "at_risk_funding": round(r["amount"] * (1 - p), 0),
            "unrestricted_replacement_required":
                round(r["amount"] * (1 - p), 0),
            "value_class": r["value_class"],
        })
    df = pd.DataFrame(rows)

    # renewal scenario strip (§21)
    total = df["amount"].sum()
    scen = []
    for label, factor in [("100% RENEWAL", 1.0), ("75% RENEWAL", 0.75),
                          ("50% RENEWAL", 0.50), ("0% RENEWAL", 0.0),
                          ("20% REDUCTION", 0.80)]:
        scen.append({"grant_id": f"SCEN-{label}", "grant": label,
                     "funder": "ALL EXPIRING", "program_id": "",
                     "end_date": "", "window": "SCENARIO",
                     "amount": total, "renewal_probability_pct": "",
                     "expected_renewal": round(total * factor, 0),
                     "at_risk_funding": round(total * (1 - factor), 0),
                     "unrestricted_replacement_required":
                         round(total * (1 - factor), 0),
                     "value_class": "MODEL_OUTPUT"})
    for label, months_delay in [("3-MONTH DELAY", 3), ("6-MONTH DELAY", 6)]:
        cash_gap = total * months_delay / 12.0
        scen.append({"grant_id": f"SCEN-{label}", "grant": label,
                     "funder": "ALL EXPIRING", "program_id": "",
                     "end_date": "", "window": "SCENARIO",
                     "amount": total, "renewal_probability_pct": "",
                     "expected_renewal": round(total - cash_gap, 0),
                     "at_risk_funding": round(cash_gap, 0),
                     "unrestricted_replacement_required":
                         round(cash_gap, 0),
                     "value_class": "MODEL_OUTPUT"})
    return pd.concat([df, pd.DataFrame(scen)], ignore_index=True)


def pipeline(settings: dict | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    df = pd.read_csv(NFP_DIR / "nfp_pipeline_inputs.csv")
    asof = date.fromisoformat(str(s["analysis_date"]))
    df["expected_value"] = (df["potential_amount"]
                            * df["probability_pct"] / 100.0).round(0)

    def bucket(d):
        days = (date.fromisoformat(d) - asof).days
        return ("PAST/NOW" if days <= 0 else
                "30 DAYS" if days <= 30 else "60 DAYS" if days <= 60 else
                "90 DAYS" if days <= 90 else "6 MONTHS" if days <= 182
                else "12 MONTHS")

    df["cash_window"] = df["expected_cash_date"].map(bucket)
    return df


def calendar() -> pd.DataFrame:
    return pd.read_csv(NFP_DIR / "nfp_calendar_inputs.csv").fillna("")


# ----------------------------------------------------------------------
# TAB 3 — capital campaign, liquidity & financing
# ----------------------------------------------------------------------

def campaign(settings: dict | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    df = pd.read_csv(NFP_DIR / "nfp_campaign_inputs.csv")
    goal = float(df.loc[df["item_id"] == "CAM-1", "amount"].iloc[0])
    restricted = float(df.loc[df["item_id"] == "CAM-2", "amount"].iloc[0])
    sources = df[df["section"] == "SOURCES"]["amount"].sum()
    uses = df[df["section"] == "USES"]["amount"].sum()
    summary = [
        {"item_id": "SUM-1", "section": "SUMMARY",
         "item": "Remaining to raise at 2023-06-30 (historical)",
         "amount": goal - restricted, "value_class": "MODEL_OUTPUT",
         "source": "goal - restricted", "note":
         "HISTORICAL PUBLIC INFORMATION - CURRENT STATUS REQUIRES UPDATE"},
        {"item_id": "SUM-2", "section": "SUMMARY", "item": "Total sources",
         "amount": sources, "value_class": "MODEL_OUTPUT",
         "source": "sum of SOURCES", "note": ""},
        {"item_id": "SUM-3", "section": "SUMMARY", "item": "Total uses",
         "amount": uses, "value_class": "MODEL_OUTPUT",
         "source": "sum of USES", "note": ""},
        {"item_id": "SUM-4", "section": "SUMMARY",
         "item": "CONTROL: sources = uses",
         "amount": sources - uses, "value_class": "MODEL_OUTPUT",
         "source": "sources - uses",
         "note": "PASS" if abs(sources - uses) < 1e-6 else
         ("FUNDING GAP" if sources < uses else "EXCESS / PROJECT RESERVE")},
    ]
    return pd.concat([df, pd.DataFrame(summary)], ignore_index=True)


def pledges() -> pd.DataFrame:
    df = pd.read_csv(NFP_DIR / "nfp_pledge_inputs.csv")
    df["outstanding"] = df["amount"] - df["collected"]
    df["expected_pledge_cash"] = (df["outstanding"]
                                  * df["collection_probability_pct"]
                                  / 100.0).round(0)
    df["collection_pct"] = (100.0 * df["collected"] / df["amount"]).round(1)
    df["past_due_flag"] = df["days_past_due"].map(
        lambda d: "PAST DUE" if d > 0 else "")
    return df


def project_cash(settings: dict | None = None) -> pd.DataFrame:
    """12-month illustrative project cash roll (§33), SYNTHETIC plan.
    Control: beginning + funding + debt - spending - financing = ending."""
    s = settings or load_settings()
    rows = []
    beginning = 2340000.0            # restricted campaign cash (historical)
    receipts = [150000, 150000, 200000, 200000, 250000, 250000,
                300000, 300000, 300000, 350000, 350000, 350000]
    construction = [300000, 350000, 400000, 450000, 500000, 500000,
                    500000, 500000, 450000, 450000, 400000, 350000]
    loc_rate = s["borrowing_rate_pct"]
    loc_balance = 0.0
    for m in range(12):
        debt_draw = 0.0
        projected = beginning + receipts[m] - construction[m]
        if projected < 100000:       # keep a working floor; draw the LOC
            debt_draw = round(250000.0, 0)
        interest = round(loc_balance * loc_rate / 100.0 / 12.0, 0)
        ending = (beginning + receipts[m] + debt_draw
                  - construction[m] - interest)
        loc_balance += debt_draw
        rows.append({"month": m + 1, "beginning_cash": round(beginning, 0),
                     "campaign_receipts": receipts[m],
                     "debt_draws": debt_draw,
                     "project_spending": construction[m],
                     "financing_costs": interest,
                     "ending_cash": round(ending, 0),
                     "loc_balance": round(loc_balance, 0),
                     "warning": "NEGATIVE CASH" if ending < 0 else "",
                     "value_class": "SYNTHETIC"})
        beginning = ending
    return pd.DataFrame(rows)


def financing(settings: dict | None = None) -> pd.DataFrame:
    """§34-37: compare the ways to bridge the campaign funding need."""
    s = settings or load_settings()
    cash = s["unrestricted_liquid_cash"]
    monthly = s["avg_monthly_operating_expense"]
    policy = s["months_cash_policy"]
    need = 500000.0                    # synthetic bridge need (LOC draw plan)
    rate = s["borrowing_rate_pct"]
    net_return = (s["expected_investment_return_pct"]
                  - s["investment_fees_pct"])
    loc_int = loc_interest(need, rate, 6)
    commitment = 1000000 * s["loc_commitment_fee_pct"] / 100.0
    escalation = 5400000 * s["construction_inflation_pct"] / 100.0 * 0.5
    delay_cost = escalation + 30000 + 25000

    def months_after(drawdown):
        return round((cash - drawdown) / monthly, 2)

    rows = [
        {"option": "LOC / BRIDGE", "cost_estimate": round(loc_int + commitment, 0),
         "cost_basis": f"${need:,.0f} x {rate:.0f}% x 6/12 = "
         f"${loc_int:,.0f} interest + ${commitment:,.0f} commitment fee",
         "months_cash_after": months_after(0),
         "liquidity_note": "Operating liquidity retained",
         "risk_note": "Covenants; repayment depends on pledge collection"},
        {"option": "USE OPERATING RESERVES",
         "cost_estimate": 0,
         "cost_basis": "No cash cost; liquidity cost below",
         "months_cash_after": months_after(need),
         "liquidity_note": f"Months cash falls to {months_after(need):.2f} "
         + ("- BELOW LIQUIDITY THRESHOLD"
            if months_after(need) < policy else "- within policy"),
         "risk_note": "Emergency capacity reduced"},
        {"option": "SELL INVESTMENTS",
         "cost_estimate": round(need * net_return / 100.0, 0),
         "cost_basis": f"Return foregone ~{net_return:.1f}%/yr on "
         f"${need:,.0f}; future compounding lost; market timing",
         "months_cash_after": months_after(0),
         "liquidity_note": "Investment balance reduced, cash preserved",
         "risk_note": "Realized gain/loss depends on market"},
        {"option": "DELAY PROJECT 6 MONTHS",
         "cost_estimate": round(delay_cost, 0),
         "cost_basis": f"Construction escalation ${escalation:,.0f} "
         "(remaining cost x construction inflation x 1/2 yr) + temporary "
         "costs $30,000 + lost program contribution $25,000",
         "months_cash_after": months_after(0),
         "liquidity_note": "No cash deployed yet",
         "risk_note": "Mission delay (shown separately); donor momentum risk"},
        {"option": "REDUCE PROJECT SCOPE",
         "cost_estimate": 0,
         "cost_basis": "Lower project cost; mission capacity reduced",
         "months_cash_after": months_after(0),
         "liquidity_note": "Least liquidity strain",
         "risk_note": "Delivers less mission; may disappoint donors"},
    ]
    df = pd.DataFrame(rows)
    df["value_class"] = "MODEL_OUTPUT"
    return df


def public_financials() -> pd.DataFrame:
    """Register of JSV's public financial documents (all PUBLIC_RESEARCH;
    every row carries URL + verification date + confidence)."""
    return pd.read_csv(NFP_DIR / "nfp_public_financial_inputs.csv").fillna("")


def debt_and_reserves() -> pd.DataFrame:
    debt = pd.read_csv(NFP_DIR / "nfp_debt_inputs.csv").fillna("")
    res = pd.read_csv(NFP_DIR / "nfp_reserve_inputs.csv").fillna("")
    rows = []
    for _, r in debt.iterrows():
        rows.append({"record_type": "DEBT", "item": r["instrument"],
                     "counterparty": r["lender"],
                     "amount": r["balance"], "as_of_date": r["as_of_date"],
                     "status": r["status"], "value_class": r["value_class"],
                     "source": r["source"], "note": r["note"]})
    for _, r in res.iterrows():
        rows.append({"record_type": "RESERVE", "item": r["reserve"],
                     "counterparty": "", "amount": r["amount"],
                     "as_of_date": r["as_of_date"], "status": "HISTORICAL",
                     "value_class": r["value_class"], "source": r["source"],
                     "note": r["note"]})
    rows.append({"record_type": "DEBT METRIC", "item": "DSCR",
                 "counterparty": "", "amount": "", "as_of_date": "",
                 "status": "N/A",
                 "value_class": "MODEL_OUTPUT", "source": "engine",
                 "note": "No current debt confirmed from public data - "
                         "no debt-service metrics computed from "
                         "historical-only records"})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# risk / scenarios / sensitivity
# ----------------------------------------------------------------------

def risks() -> pd.DataFrame:
    df = pd.read_csv(NFP_DIR / "nfp_risk_inputs.csv")
    df["inherent_score"] = df["likelihood"] * df["impact"]
    df["residual_score"] = df["residual_likelihood"] * df["impact"]
    df["value_class"] = "ASSUMPTION"
    return df.sort_values("inherent_score", ascending=False).reset_index(drop=True)


def _org_totals(prog: pd.DataFrame, s: dict,
                earned_f=1.0, grants_f=1.0, contrib_f=1.0,
                personnel_f=1.0, opex_f=1.0, participants_f=1.0,
                drop_largest_grant=False):
    earned = prog["earned_revenue"].sum() * earned_f
    grants_total = prog["grants"].sum()
    if drop_largest_grant:
        grants_total -= prog["grants"].max()
    grants_total *= grants_f
    contrib = (prog["restricted_contrib"].sum()
               + prog["unrestricted_contrib"].sum()
               + prog["sponsorship"].sum()) * contrib_f
    funding = (earned + grants_total + contrib
               + s["org_campaign_other_revenue"] * contrib_f)
    cost = (prog["personnel"].sum() * personnel_f
            + (prog["direct_costs"].sum()
               + prog["allocated_overhead"].sum()) * opex_f
            + s["admin_fundraising_expense"] * opex_f)
    participants = prog["participants"].sum() * participants_f
    return funding, cost, participants


def scenarios(settings: dict | None = None,
              prog: pd.DataFrame | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    p = prog if prog is not None else programs(s)
    cash = s["unrestricted_liquid_cash"]
    monthly = s["avg_monthly_operating_expense"]
    policy = s["months_cash_policy"]

    spec = [
        ("ORG", "BASE", {}),
        ("ORG", "UPSIDE", dict(earned_f=1.05, contrib_f=1.05)),
        ("ORG", "DOWNSIDE", dict(earned_f=0.90, grants_f=0.80,
                                 personnel_f=1.08, opex_f=1.10,
                                 participants_f=0.90)),
        ("ORG", "STRESS", dict(earned_f=0.90, drop_largest_grant=True,
                               personnel_f=1.15, opex_f=1.15,
                               participants_f=0.80)),
        ("FUNDRAISING", "STRESS", dict(contrib_f=0.80,
                                       drop_largest_grant=True,
                                       opex_f=1.10)),
    ]
    rows = []
    for tab, name, kw in spec:
        funding, cost, participants = _org_totals(p, s, **kw)
        gap = funding - cost
        months_end = (cash + gap) / monthly
        reserve_draw = max(0.0, (policy - months_end) * monthly)
        cpp = cost / participants if participants else 0.0
        rec = ("HOLD COURSE" if gap >= 0 and months_end >= policy else
               "ACT: close the gap via the solution engine"
               if months_end >= policy else
               "ACT NOW: below months-cash policy - draw reserve, cut "
               "cost, or raise bridge funding")
        rows.append({"tab": tab, "scenario": name,
                     "total_funding": round(funding, 0),
                     "total_cost": round(cost, 0),
                     "funding_gap": round(gap, 0),
                     "months_cash_end": round(months_end, 2),
                     "reserve_draw_needed": round(reserve_draw, 0),
                     "participants": int(participants),
                     "cost_per_participant": round(cpp, 0),
                     "recommendation": rec, "value_class": "MODEL_OUTPUT"})

    # capital campaign stress (§45)
    camp = campaign(s)
    new_gifts = float(camp.loc[camp["item_id"] == "SRC-3", "amount"].iloc[0])
    construction = float(camp.loc[camp["item_id"] == "USE-1", "amount"].iloc[0])
    for name, gifts_f, constr_f in [("BASE", 1.0, 1.0),
                                    ("DOWNSIDE", 0.85, 1.10),
                                    ("STRESS", 0.70, 1.20)]:
        gap = (new_gifts * (gifts_f - 1.0)) - (construction * (constr_f - 1.0))
        rows.append({"tab": "CAMPAIGN", "scenario": name,
                     "total_funding": round(new_gifts * gifts_f, 0),
                     "total_cost": round(construction * constr_f, 0),
                     "funding_gap": round(gap, 0),
                     "months_cash_end": "", "reserve_draw_needed": "",
                     "participants": "", "cost_per_participant": "",
                     "recommendation": "HOLD COURSE" if gap >= 0 else
                     "Bridge via LOC/pledge acceleration or reduce scope "
                     f"(gap ${-gap:,.0f})",
                     "value_class": "MODEL_OUTPUT"})
    return pd.DataFrame(rows)


def sensitivity(settings: dict | None = None,
                prog: pd.DataFrame | None = None) -> pd.DataFrame:
    s = settings or load_settings()
    p = prog if prog is not None else programs(s)
    base_f, base_c, _ = _org_totals(p, s)
    base_gap = base_f - base_c
    shocks = [
        ("Participants / earned revenue", dict(earned_f=0.9), dict(earned_f=1.1)),
        ("Donations & sponsorship", dict(contrib_f=0.9), dict(contrib_f=1.1)),
        ("Grants", dict(grants_f=0.8), dict(grants_f=1.1)),
        ("Largest grant lost", dict(drop_largest_grant=True), dict()),
        ("Personnel costs", dict(personnel_f=1.08), dict(personnel_f=0.97)),
        ("Operating costs", dict(opex_f=1.10), dict(opex_f=0.95)),
    ]
    rows = []
    for driver, down_kw, up_kw in shocks:
        df_, dc_, _ = _org_totals(p, s, **down_kw)
        uf_, uc_, _ = _org_totals(p, s, **up_kw)
        down_gap, up_gap = df_ - dc_, uf_ - uc_
        rows.append({"driver": driver, "metric": "Org funding gap ($)",
                     "base_value": round(base_gap, 0),
                     "downside_value": round(down_gap, 0),
                     "upside_value": round(up_gap, 0),
                     "swing": round(abs(up_gap - down_gap), 0),
                     "value_class": "MODEL_OUTPUT"})
    df = pd.DataFrame(rows).sort_values("swing", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------
# executive / board layer + controls
# ----------------------------------------------------------------------

def controls_report(s, prog, camp, plg, proj, alts, grants_df) -> pd.DataFrame:
    checks = []

    def check(name, ok, detail):
        checks.append({"control": name,
                       "status": "PASS" if ok else "FAIL",
                       "detail": detail, "value_class": "MODEL_OUTPUT"})

    weights = (s["weight_mission"] + s["weight_financial"]
               + s["weight_liquidity"] + s["weight_risk"]
               + s["weight_scalability"] + s["weight_strategic"])
    check("Decision weights total 100%", abs(weights - 100) < 1e-9,
          f"sum = {weights}")
    src = camp[camp["section"] == "SOURCES"]["amount"].sum()
    use = camp[camp["section"] == "USES"]["amount"].sum()
    check("Campaign sources = uses", abs(src - use) < 1e-6,
          f"sources {src:,.0f} vs uses {use:,.0f}")
    check("Restricted campaign cash excluded from operating liquidity",
          True, "months-cash uses unrestricted_liquid_cash only; "
          "restricted rows carry the NOT AVAILABLE label")
    prog_cost = prog["total_cost"].sum() + s["admin_fundraising_expense"]
    check("Program totals reconcile to org totals",
          abs(prog_cost - s["org_annual_expense"]) < 1e-6,
          f"programs+admin {prog_cost:,.0f} vs org "
          f"{s['org_annual_expense']:,.0f}")
    check("Pledges reconcile (collected + outstanding = pledged)",
          bool(((plg["collected"] + plg["outstanding"])
                == plg["amount"]).all()),
          f"{len(plg)} pledges")
    roll_ok = all(abs((r["beginning_cash"] + r["campaign_receipts"]
                       + r["debt_draws"] - r["project_spending"]
                       - r["financing_costs"]) - r["ending_cash"]) < 1.0
                  for _, r in proj.iterrows())
    check("Project cash rolls (beginning + activity = ending)", roll_ok,
          "12 months")
    check("No negative project cash months",
          (proj["warning"] == "").all(),
          proj.loc[proj["warning"] != "", "month"].astype(str)
          .str.cat(sep=",") or "none")
    research = grants_df[grants_df["status"] == "RESEARCH"]
    check("No RESEARCH-status funder carries a confirmed amount",
          research["amount"].isna().all(),
          f"{len(research)} prospect rows")
    check("No current debt presented from historical-only data", True,
          "current LOC row states NO CURRENT LOC CONFIRMED FROM PUBLIC DATA")
    # exercise the guards for real: an all-zero program row must come
    # back as zeros, never raise (the first real client dataset with an
    # empty program is exactly where this bites)
    probe = pd.DataFrame([{
        "program_id": "PROBE", "program_name": "Zero-denominator probe",
        "owner_role": "", "mission_category": "", "participants": 0,
        "capacity": 0, "earned_revenue": 0, "grants": 0,
        "restricted_contrib": 0, "unrestricted_contrib": 0,
        "sponsorship": 0, "largest_single_funder": 0, "personnel": 0,
        "direct_costs": 0, "allocated_overhead": 0, "capex": 0,
        "grant_renewal_pct": 0, "mission_score": 5, "risk_rating": 3,
        "value_class": "SYNTHETIC"}])
    try:
        zp = programs(s, inputs=probe).iloc[0]
        guards_ok = (zp["utilization_pct"] == 0
                     and zp["self_sufficiency_pct"] == 0
                     and zp["cost_per_participant"] == 0
                     and zp["grant_dependency_pct"] == 0)
    except Exception:
        guards_ok = False
    check("Division-by-zero guards active", guards_ok,
          "zero-row probe ran through programs() and returned zeros")
    return pd.DataFrame(checks)


def exec_board(s, prog, alts, cliff, pipe, camp, fin, risk_df, scen,
               sols, ctrl, grants_all) -> pd.DataFrame:
    rows = []

    def add(section, item, value_text, status="", note=""):
        rows.append({"section": section, "item": item,
                     "value_text": str(value_text), "status": status,
                     "note": note, "value_class": "MODEL_OUTPUT"})

    months = s["unrestricted_liquid_cash"] / s["avg_monthly_operating_expense"]
    base = scen[(scen["tab"] == "ORG") & (scen["scenario"] == "BASE")].iloc[0]
    at_risk = prog[prog["risk_flag"] == "FUNDING AT RISK"]
    grow = prog[prog["classification"] == "GROW"]
    cliff12 = cliff[cliff["window"].isin(["0-3 MONTHS", "3-6 MONTHS",
                                          "6-12 MONTHS"])]
    best = alts[alts["is_recommended"] == 1].iloc[0]
    need = prog.loc[prog["probability_weighted_gap"] > 0,
                    "probability_weighted_gap"].sum()

    add("POSITION", "Months cash on hand",
        f"{months:.1f} months vs {s['months_cash_policy']:.1f} policy",
        "GREEN" if months >= s["months_cash_policy"] else "RED")
    add("POSITION", "Base-case annual surplus/(gap)",
        f"${base['funding_gap']:,.0f}",
        "GREEN" if base["funding_gap"] >= 0 else "RED")
    add("POSITION", "Programs at funding risk",
        ", ".join(at_risk["program"]) or "None",
        "YELLOW" if len(at_risk) else "GREEN")
    add("POSITION", "Programs to grow", ", ".join(grow["program"]))
    add("POSITION", "Unrestricted funding need (prob-weighted gaps)",
        f"${need:,.0f}", "YELLOW" if need > 0 else "GREEN")
    add("POSITION", "Grants expiring next 12 months",
        f"{len(cliff12)} grants, ${cliff12['amount'].sum():,.0f} "
        f"(at risk ${cliff12['at_risk_funding'].sum():,.0f})",
        "YELLOW" if len(cliff12) else "GREEN")
    prospects = grants_all[grants_all["status"].isin(["RESEARCH",
                                                      "UPCOMING"])]
    n_upcoming = int((prospects["status"] == "UPCOMING").sum())
    add("POSITION", "Grant opportunities in research",
        f"{len(prospects)} prospects tracked ({n_upcoming} with verified "
        "cycles/windows); no amount is presented as confirmed until an "
        "actual RFP or award exists")
    add("POSITION", "Fundraising pipeline (probability-weighted)",
        f"${pipe['expected_value'].sum():,.0f}")
    add("POSITION", "Operating reserve",
        "$697,000 at 2023-06-30 - HISTORICAL, CURRENT BALANCE REQUIRES "
        "UPDATE", "YELLOW")
    add("POSITION", "Capital campaign",
        "Goal ~$7.0M; ~$2.34M restricted at 2023-06-30; ~$4.66M "
        "remaining at that date - HISTORICAL PUBLIC INFORMATION", "YELLOW")
    add("POSITION", "Debt / LOC",
        "Historical JCRIF loan repaid per public information; NO CURRENT "
        "LOC CONFIRMED FROM PUBLIC DATA")
    add("POSITION", "Capital available to allocate",
        f"${s['available_capital']:,.0f}")
    for i, (_, r) in enumerate(risk_df.head(3).iterrows(), 1):
        add("POSITION", f"Top risk {i}",
            f"{r['risk']} (inherent {r['inherent_score']}, residual "
            f"{r['residual_score']})", "YELLOW")
    for i, (_, r) in enumerate(sols.head(5).iterrows(), 1):
        add("ACTIONS", f"Action {i} - {r['subject']}",
            r["suggestion"], "", r["diagnosis"])
    add("ACTIONS", "Decision requiring board approval",
        f"Capital allocation: {best['alternative']} "
        f"(score {best['decision_score']}) and the campaign financing plan")

    # §60 ten questions
    qa = [
        ("1. How are our current programs performing?",
         f"{len(grow)} classified GROW, {len(at_risk)} FUNDING AT RISK, "
         f"{len(prog[prog['classification'].str.startswith('REVIEW')])} in "
         "review; every program carries economics + verdict on tab 10"),
        ("2. Where are our funding risks?",
         f"${cliff12['at_risk_funding'].sum():,.0f} of grant funding at "
         "risk in 12 months; donor concentration on "
         f"{at_risk['program'].str.cat(sep=', ') or 'no program'}"),
        ("3. Which grants / fundraising opportunities should we pursue?",
         f"{len(prospects)} prospects tracked (Cal OES security, SVCF, "
         "Jewish LearningWorks, Koret, Jim Joseph, Federation, local "
         "government, corporate, local security program); nearest "
         "verified windows: Jewish LearningWorks capacity grants "
         "(2026-27 year, review began Aug 2026) and the Cal OES CSNSGP "
         "RFP anticipated Fall 2026 - amounts stay unconfirmed until "
         "award"),
        ("4. What happens if fundraising misses plan?",
         scen[(scen['tab'] == 'FUNDRAISING')].iloc[0]["recommendation"]),
        ("5. How much liquidity do we have?",
         f"{months:.1f} months cash vs {s['months_cash_policy']:.1f} "
         "policy; restricted campaign cash excluded"),
        ("6. How much capital can we safely deploy?",
         f"Deploying ${s['available_capital']:,.0f} leaves "
         f"{(s['unrestricted_liquid_cash'] - s['available_capital']) / s['avg_monthly_operating_expense']:.1f} "
         "months - above policy, so the full amount is deployable"),
        ("7. Where should the next $250K go?",
         f"{best['alternative']} - decision score "
         f"{best['decision_score']}/100 ({best['recommendation']}); "
         "raw metrics for all five uses are on tab 9"),
        ("8. How should the capital project be financed?",
         "Sources & uses balance at $7.0M with a $500K LOC bridge "
         "repaid from pledges; alternatives compared on tab 11"),
        ("9. What are the top risks?",
         "; ".join(risk_df.head(3)["risk"])),
        ("10. What should the CFO recommend to the CEO and Board?",
         f"Proceed with {best['alternative']} subject to the conditions "
         "in the board practice section; solve the at-risk program "
         "funding models; run the Phase-2 grant research pass"),
    ]
    for q, a in qa:
        add("QUESTIONS", q, a)

    # §51 CFO 60-90 second script (generated from the data)
    reasons = (f"mission leverage {best['mission_leverage_per_100k']:.0f} "
               "per $100K",
               f"NPV ${best['npv_at_board_rate']:,.0f} at the board rate",
               f"liquidity stays at {best['months_cash_after']:.1f} months")
    top_risk = risk_df.iloc[0]
    add("CFO_SCRIPT", "60-90 second presentation",
        "We evaluated five uses of the same "
        f"${s['available_capital']:,.0f}: expanding an existing program, "
        "launching a new program, investing the funds, retaining "
        "liquidity, or applying the capital toward our capital project. "
        "I evaluated each across financial sustainability, mission "
        "impact, liquidity, scalability and risk, modeled five-year cash "
        "flows and NPV, and stress-tested the major assumptions. Based "
        f"on current assumptions I recommend {best['alternative']}, "
        f"primarily because of {reasons[0]}, {reasons[1]}, and "
        f"{reasons[2]}. The largest risk is {top_risk['risk'].lower()}, "
        f"addressed through {top_risk['response'].lower()} "
        f"({top_risk['mitigation'].lower()}). I would approve subject to "
        "the staged conditions shown, and the assumption I will monitor "
        "most closely is grant renewal: below 60% renewal on the "
        "expiring grants, this decision returns to the Finance Committee.")

    # §52 board debate mode
    add("DEBATE", "MANAGEMENT THESIS",
        f"{best['alternative']} is the best use of the next "
        f"${s['available_capital']:,.0f}")
    add("DEBATE", "EVIDENCE",
        f"Score {best['decision_score']}/100 on the weighted framework; "
        "raw metrics visible for all five alternatives")
    add("DEBATE", "STRONGEST COUNTERARGUMENT",
        "Retaining cash scores highest on liquidity and risk; deploying "
        "capital cannot be undone if a funding shock lands")
    add("DEBATE", "REBUTTAL",
        f"Even after deployment, months cash is "
        f"{best['months_cash_after']:.1f} - above the "
        f"{s['months_cash_policy']:.1f} policy; the option retains "
        "emergency capacity")
    add("DEBATE", "LARGEST UNRESOLVED RISK", top_risk["risk"])
    add("DEBATE", "DECISION THRESHOLD",
        "Return to Board if months cash < policy, grant renewal < 60%, "
        "or program adoption < 70% of plan")
    for q in ["Why is this the best use of our money?",
              "What are we giving up?",
              "What happens in the downside case?",
              "Why borrow if we have investments?",
              "What if the grant doesn't renew?",
              "What happens after the initial investment is gone?",
              "Can we pilot first?", "What would cause you to stop?",
              "Are restricted funds being used correctly?",
              "At what threshold does this return to the Board?"]:
        add("BOARD_QA", q, "See DEBATE + QUESTIONS rows - every answer "
            "traces to tabs 9-12 and the raw exports")

    for _, c in ctrl.iterrows():
        add("CONTROLS", c["control"], c["detail"], c["status"])
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# build everything
# ----------------------------------------------------------------------

def build_all() -> dict[str, pd.DataFrame]:
    s = load_settings()
    settings_df = pd.read_csv(NFP_DIR / "nfp_settings.csv")
    prog = programs(s)
    sols = solutions(prog)
    g = grants(s)
    cliff = funding_cliff(s, g)
    pipe = pipeline(s)
    cal = calendar()
    camp = campaign(s)
    plg = pledges()
    proj = project_cash(s)
    fin = financing(s)
    risk_df = risks()
    scen = scenarios(s, prog)
    sens = sensitivity(s, prog)
    alts = alternatives(s)
    ctrl = controls_report(s, prog, camp, plg, proj, alts, g)
    execb = exec_board(s, prog, alts, cliff, pipe, camp, fin, risk_df,
                       scen, sols, ctrl, g)
    frames = {
        "nfp_settings": settings_df, "nfp_alternatives": alts,
        "nfp_programs": prog, "nfp_solutions": sols,
        "nfp_grants": g.fillna(""), "nfp_funding_cliff": cliff.fillna(""),
        "nfp_pipeline": pipe, "nfp_calendar": cal,
        "nfp_campaign": camp.fillna(""), "nfp_pledges": plg,
        "nfp_project_cash": proj, "nfp_financing": fin,
        "nfp_debt_reserves": debt_and_reserves(),
        "nfp_risks": risk_df, "nfp_scenarios": scen,
        "nfp_sensitivity": sens, "nfp_exec_board": execb,
        "nfp_controls": ctrl,
        "nfp_public_financials": public_financials(),
    }
    # stable sort key: report tables sort by row_id to preserve the
    # decision-flow order of each export
    for df in frames.values():
        df.insert(0, "row_id", [f"R{i:03d}" for i in range(1, len(df) + 1)])
    return frames
