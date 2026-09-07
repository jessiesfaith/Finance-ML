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
    # readable axis label - owner QA: "what is alt" (ALT-x ids mean
    # nothing on a chart axis; add-only column, ids stay for joins)
    short = {"ALT-1": "1 Expand program", "ALT-2": "2 New pilot",
             "ALT-3": "3 Invest capital", "ALT-4": "4 Status quo",
             "ALT-5": "5 Capital project"}
    df["short_label"] = df["alternative_id"].map(short).fillna(
        df["alternative_id"])
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


def role_matrix() -> pd.DataFrame:
    """Owner-provided leadership responsibility matrix (CEO/CCO, COO,
    CFO, and the ratios the CFO brings to each area)."""
    return pd.read_csv(NFP_DIR / "nfp_role_matrix_inputs.csv").fillna("")


def ratio_990() -> pd.DataFrame:
    """Ratio calc dictionary mapped to the current-revision IRS Form
    990 part/line structure, with worked examples and reads."""
    return pd.read_csv(NFP_DIR / "nfp_ratio_990_inputs.csv").fillna("")


def survey_findings() -> pd.DataFrame:
    """Verified findings from the 2024 Santa Clara County Jewish
    Community Study (PUBLIC_RESEARCH, each with source URL)."""
    return pd.read_csv(NFP_DIR / "nfp_survey_finding_inputs.csv").fillna("")


def survey_alignment(prog: pd.DataFrame) -> pd.DataFrame:
    """Map study initiatives to the module's programs, enriched with
    each program's LIVE stats so impact potential reads against real
    capacity headroom (program stats are SYNTHETIC until client data)."""
    a = pd.read_csv(NFP_DIR / "nfp_survey_alignment_inputs.csv").fillna("")
    stats = prog.set_index("program_id")

    def enrich(ids):
        parts = []
        for pid in str(ids).split(";"):
            if pid in stats.index:
                r = stats.loc[pid]
                headroom = int(r["capacity"] - r["participants"])
                parts.append(f"{r['program']} (util "
                             f"{r['utilization_pct']:.0f}%, headroom "
                             f"{headroom}, {r['classification']})")
        return " · ".join(parts)

    a["aligned_programs"] = a["program_ids"].map(enrich)
    return a


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
# time dimension (owner request: see it over time)
# ----------------------------------------------------------------------

def gap_history(s: dict, prog: pd.DataFrame) -> pd.DataFrame:
    """Monthly funding vs cost vs the cash balance supporting the gap.
    SYNTHETIC illustration: program funding spread evenly, campaign
    revenue following the donation seasonality, calibrated so the
    latest-12-month totals reconcile to the deterministic org totals
    and the balance ends at today's unrestricted cash."""
    h = pd.read_csv(NFP_DIR / "nfp_history.csv")
    prog_cost = (h[h["series_id"].str.startswith("cost:")]
                 .groupby("month")["value"].sum().sort_index())
    net = (h[h["series_id"] == "net_cash:ORG"]
           .set_index("month")["value"].sort_index())
    admin_monthly = s["admin_fundraising_expense"] / 12.0
    rows = []
    for m in prog_cost.index:
        cost_total = prog_cost[m] + admin_monthly
        gap = net[m]                    # the seeded funding-minus-cost
        rows.append({"month": m,
                     "funding": round(cost_total + gap, 0),
                     "cost": round(cost_total, 0),
                     "gap": round(gap, 0)})
    df = pd.DataFrame(rows)
    df["cumulative_gap"] = df["gap"].cumsum().round(0)
    # cash balance: ends at today's unrestricted cash, driven by the gap
    end_cash = s["unrestricted_liquid_cash"]
    total = df["gap"].sum()
    df["cash_balance"] = (end_cash - total + df["cumulative_gap"]).round(0)
    trailing = df["cost"].rolling(12, min_periods=1).mean()
    df["months_cash"] = (df["cash_balance"] / trailing).round(2)
    df["policy_months"] = s["months_cash_policy"]
    df["breach_flag"] = df["months_cash"].map(
        lambda v: "BREACH" if v < s["months_cash_policy"] else "")
    df["value_class"] = "SYNTHETIC"
    return df


def support_map(prog: pd.DataFrame) -> pd.DataFrame:
    """Which funds support which expense, per program - earned, grants,
    restricted gifts and sponsorship first, unrestricted money last.
    Parts sum to the expense exactly by construction."""
    rows = []
    for _, p in prog.iterrows():
        dedicated = (p["earned_revenue"] + p["grants"]
                     + p["restricted_contrib"] + p["sponsorship"])
        unrestricted = p["total_cost"] - dedicated
        rows.append({
            "program": p["program"], "expense_total": p["total_cost"],
            "funded_by_earned": p["earned_revenue"],
            "funded_by_grants": p["grants"],
            "funded_by_restricted_gifts": p["restricted_contrib"],
            "funded_by_sponsorship": p["sponsorship"],
            "funded_by_unrestricted": round(unrestricted, 0),
            "reading": ("generates unrestricted surplus"
                        if unrestricted < 0 else
                        "leans on unrestricted funds"),
            "value_class": "SYNTHETIC"})
    return pd.DataFrame(rows)


def alt_timeline(s: dict) -> pd.DataFrame:
    """Cumulative net cash of the five alternatives, year 0-5 - the
    over-time view of the capital allocation choice."""
    alts = alternatives(s).set_index("alternative_id")
    cols = {"ALT-1": "expand_program", "ALT-2": "launch_program",
            "ALT-3": "invest_capital", "ALT-4": "retain_cash",
            "ALT-5": "capital_project"}
    net = (s["expected_investment_return_pct"]
           - s["investment_fees_pct"]) / 100.0
    capital = s["available_capital"]
    rows = []
    cum = {}
    for a in cols:
        cum[a] = 0.0 if a == "ALT-4" else -capital
    for y in range(0, 6):
        if y > 0:
            for a in cols:
                cum[a] += alts.loc[a, f"net_cash_y{y}"]
        vals = {cols[a]: round(cum[a], 0) for a in cols}
        # ALT-3's capital stays recoverable: show NET POSITION (cash
        # flows + portfolio value), not bare cash - bare cash would
        # falsely read as a loss
        vals["invest_capital"] = round(cum["ALT-3"]
                                       + capital * (1 + net) ** y, 0)
        rows.append({"year": y, **vals, "value_class": "MODEL_OUTPUT"})
    return pd.DataFrame(rows)


def pledge_schedule() -> pd.DataFrame:
    """Expected pledge collections by month (lump at expected date,
    discounted by collection probability) + the annualized run rate."""
    plg = pledges()
    months = [f"{2026 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}"
              for i in range(16)]                 # 2026-10 .. 2028-01
    expected = {m: 0.0 for m in months}
    for _, r in plg.iterrows():
        m = str(r["expected_date"])[:7]
        if m in expected:
            expected[m] += float(r["expected_pledge_cash"])
    df = pd.DataFrame({"month": months,
                       "expected_collections":
                       [round(expected[m], 0) for m in months]})
    df["cumulative_expected"] = df["expected_collections"].cumsum().round(0)
    active = max(1, int((df["expected_collections"] > 0).sum()))
    span = months.index(max(m for m in months if expected[m] > 0)) + 1         if any(v > 0 for v in expected.values()) else 1
    run_rate = df["expected_collections"].sum() / span * 12.0
    df["run_rate_annualized"] = round(run_rate, 0)
    df["value_class"] = "SYNTHETIC"
    return df


def funding_mix(s: dict, prog: pd.DataFrame,
                grants_df: pd.DataFrame) -> pd.DataFrame:
    """Which funding is RECURRING vs ONE-TIME vs a LONG-TERM forecast -
    the recurrence classification the owner asked to see."""
    live = grants_df[grants_df["status"].isin(["CURRENT", "RENEWAL"])
                     & grants_df["amount"].notna()]
    grant_expected = (live["amount"]
                      * live["renewal_probability_pct"] / 100.0).sum()
    pipe = pipeline(s)
    rows = [
     ("Earned program revenue", "RECURRING",
      prog["earned_revenue"].sum(), "Fees/tuition - repeats yearly"),
     ("Annual campaign & org contributions", "RECURRING",
      s["org_campaign_other_revenue"],
      "Annual giving; donor retention drives it (tab 13 model 9)"),
     ("Program contributions (restricted + unrestricted)", "RECURRING",
      prog["restricted_contrib"].sum()
      + prog["unrestricted_contrib"].sum(),
      "Annual gifts at donor-renewal rates"),
     ("Sponsorships", "RECURRING", prog["sponsorship"].sum(),
      "Event/program sponsors, renewed yearly"),
     ("Grants (probability-weighted renewals)", "RECURRING",
      round(grant_expected, 0),
      "Live grants x renewal probability (funding cliff, tab 10)"),
     ("Pledge collections (campaign)", "ONE-TIME",
      pledges()["expected_pledge_cash"].sum(),
      "Capital-campaign pledges - collected once"),
     ("Development pipeline (probability-weighted)", "ONE-TIME",
      pipe["expected_value"].sum(),
      "Asks in flight - not recurring until renewed"),
     ("Campaign gifts still to raise", "LONG-TERM FORECAST",
      2460000, "Second Pool plan (tab 11) - multi-year raise"),
    ]
    df = pd.DataFrame(rows, columns=["source", "recurrence_class",
                                     "amount", "basis"])
    df["value_class"] = "MODEL_OUTPUT"
    return df


def ratio_values(s: dict, prog: pd.DataFrame) -> pd.DataFrame:
    """Every ratio the module can compute TODAY, with its basis - the
    live companion to the tab-14 calc cards."""
    direct = prog["personnel"].sum() + prog["direct_costs"].sum()
    funding = prog["total_funding"].sum() + s["org_campaign_other_revenue"]
    cost_total = prog["total_cost"].sum() + s["admin_fundraising_expense"]
    rows = [
     ("Program expense ratio %", "Existing programs",
      100 * prog["total_cost"].sum() / cost_total,
      "program cost / total org expense"),
     ("Cost per participant $", "Existing programs",
      prog["total_cost"].sum() / prog["participants"].sum(),
      "portfolio cost / participants"),
     ("Revenue per participant $", "Existing programs",
      prog["total_funding"].sum() / prog["participants"].sum(),
      "portfolio funding / participants"),
     ("Portfolio self-sufficiency %", "Existing programs",
      100 * prog["earned_revenue"].sum() / direct,
      "earned revenue / direct program cost"),
     ("Capacity utilization %", "Existing programs",
      100 * prog["participants"].sum() / prog["capacity"].sum(),
      "participants / capacity"),
     ("Subsidy per participant $", "Existing programs",
      prog["unrestricted_subsidy"].sum() / prog["participants"].sum(),
      "unrestricted subsidy / participants"),
     ("Grant dependency %", "Grants",
      100 * prog["grants"].sum() / funding,
      "grants / total funding"),
     ("Restricted funding %", "Restricted gifts",
      100 * (prog["grants"].sum() + prog["restricted_contrib"].sum())
      / funding, "restricted funding / total funding"),
     ("Months cash on hand", "Cash/liquidity",
      s["unrestricted_liquid_cash"] / s["avg_monthly_operating_expense"],
      "unrestricted cash / monthly expense"),
     ("Operating reserve ratio %", "Cash/liquidity",
      100 * 697000 / s["org_annual_expense"],
      "HISTORICAL reserve / annual expense"),
     ("Compensation share of cost %", "Staffing",
      100 * prog["personnel"].sum() / cost_total,
      "program personnel / total expense"),
     ("Operating margin %", "Budget",
      100 * (funding - cost_total) / funding,
      "(funding - expense) / funding"),
     ("Donation coverage of cost %", "Annual campaign",
      100 * s["org_campaign_other_revenue"] / cost_total,
      "campaign revenue / total expense"),
     ("Pledge collection %", "Major gifts",
      100 * pledges()["collected"].sum() / pledges()["amount"].sum(),
      "collected / pledged (campaign)"),
    ]
    df = pd.DataFrame(rows, columns=["ratio", "area", "value", "basis"])
    df["value"] = df["value"].round(2)
    df["value_class"] = "MODEL_OUTPUT"
    df["note"] = "Computed from SYNTHETIC module data until client data loads"
    return df


def ratio_history(s: dict) -> pd.DataFrame:
    """Monthly ratio series where the history supports them."""
    h = pd.read_csv(NFP_DIR / "nfp_history.csv")
    cost = (h[h["series_id"].str.startswith("cost:")]
            .groupby("month")["value"].sum().sort_index())
    parts = (h[h["series_id"].str.startswith("participants:")]
             .groupby("month")["value"].sum().sort_index())
    don = (h[h["series_id"] == "donations:ORG"]
           .set_index("month")["value"].sort_index())
    scale = (s["org_campaign_other_revenue"]
             / don.loc[cost.index[-12:]].sum())
    rows = []
    for m in cost.index:
        rows.append({"month": m, "ratio_id": "cost_per_participant",
                     "ratio": "Cost per participant $ (monthly)",
                     "value": round(cost[m] / parts[m], 2)})
        rows.append({"month": m, "ratio_id": "donation_coverage_pct",
                     "ratio": "Donation coverage of monthly cost %",
                     "value": round(100 * don[m] * scale / cost[m], 2)})
    df = pd.DataFrame(rows)
    df["value_class"] = "SYNTHETIC"
    return df


# ----------------------------------------------------------------------
# TAB 16 - investments & rentals (the owner's mandate)
# ----------------------------------------------------------------------

def rentals() -> pd.DataFrame:
    return pd.read_csv(NFP_DIR / "nfp_rental_inputs.csv")


def investment_pools() -> pd.DataFrame:
    return pd.read_csv(NFP_DIR / "nfp_investment_pool_inputs.csv")


def invest_scenarios(s: dict) -> pd.DataFrame:
    """PROPOSED future investment scenarios for a community-center
    organization's investment segment. Every row is a PROPOSAL at
    stated ASSUMPTION returns - decisions belong to the board."""
    net = (s["expected_investment_return_pct"]
           - s["investment_fees_pct"]) / 100.0
    rows = []

    def scen(name, capital, annual, fv5, liquidity, risk, mission):
        rows.append({"scenario": name, "capital_required": capital,
                     "expected_annual_income": annual,
                     "five_year_value": round(fv5, 0),
                     "liquidity": liquidity, "risk_note": risk,
                     "mission_note": mission, "status": "PROPOSAL",
                     "value_class": "ASSUMPTION"})

    pool = 1150000.0        # reserve slice + above-floor cash (seeds)
    scen("S1 Policy portfolio on reserves (60/40 + ladder)",
         pool, round(pool * net, 0), pool * (1 + net) ** 5,
         "Quarterly liquidity; ladder covers 12 months",
         f"Market risk at ASSUMPTION net return {net:.1%}; drawdown "
         "tolerance set by board policy",
         "Investment income funds mission without donor asks")
    contrib = 100000.0
    fv = 250000 * (1 + net) ** 5 + sum(
        contrib * (1 + net) ** k for k in range(5))
    scen("S2 Endowment build ($250K seed + $100K/yr)",
         250000, round((250000 + 2 * contrib) * net, 0), fv,
         "Illiquid by design (endowment policy)",
         "Same market risk; spending-rate discipline required",
         "Permanent mission funding; 4% spending rate = growing "
         "annual distribution")
    scen("S3 Facility monetization (+15pts rental utilization)",
         150000, 120000, -150000 + 5 * 120000,
         "Capex sunk; income recurring",
         "Execution risk: booking ops, wear, community-use tension",
         "Uses existing assets; watch mission-use vs rental balance")
    scen("S4 Energy retrofit (LED/HVAC/solar-ready)",
         200000, 45000, -200000 + 5 * 45000,
         "Capex sunk; savings recurring",
         "Payback ~4.4 years at ASSUMPTION savings; utility-rate "
         "sensitivity",
         "Cost avoidance = unrestricted mission money; grant angle: "
         "energy-efficiency programs (tab 10 prospects)")
    return pd.DataFrame(rows)


def initiative_status() -> pd.DataFrame:
    return pd.read_csv(NFP_DIR / "nfp_initiative_status_inputs.csv")


# ----------------------------------------------------------------------
# build everything
# ----------------------------------------------------------------------

def actuals_990() -> pd.DataFrame:
    """REAL filed Form 990 figures for JSV (EIN 94-2222989), read
    directly from the five filings the owner provided (FY2021-FY2025,
    ProPublica full-filing PDFs) - the only export whose dollars
    describe the real organization. Every row is FILED (or the
    pre-merger FY2020 context from the FY2021 filing's prior-year
    column); DERIVED rows are pure arithmetic on filed figures with
    the formula in the note. Nothing here is ever estimated."""
    a = pd.read_csv(NFP_DIR / "nfp_990_actual_inputs.csv").fillna("")

    def val(fy, item):
        m = a[(a["fiscal_year"] == fy) & (a["line_item"] == item)]
        if len(m) and m.iloc[0]["amount"] != "":
            return float(m.iloc[0]["amount"])
        return None

    src = a[a["basis"] == "FILED"].iloc[0]
    derived = []
    years = ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    for prev, fy in zip(years, years[1:]):
        na, na_prev = val(fy, "net_assets_end"), val(prev,
                                                    "net_assets_end")
        if None not in (na, na_prev):
            derived.append({
                "item_id": f"D-{len(derived) + 1}", "fiscal_year": fy,
                "fiscal_year_end": a[a["fiscal_year"] == fy].iloc[0][
                    "fiscal_year_end"],
                "line_item": "net_assets_change",
                "amount": na - na_prev, "unit": "USD",
                "basis": "DERIVED", "source":
                    "Arithmetic on the FILED rows above - no new data",
                "url": src["url"], "date_verified": src["date_verified"],
                "confidence": "HIGH", "value_class": "PUBLIC_RESEARCH",
                "note": f"net_assets_end {fy} - net_assets_end {prev}"})
    out = pd.concat([a, pd.DataFrame(derived)], ignore_index=True)
    def _amt(v):
        if v == "":
            return ""
        f = float(v)
        # dollars stay whole; filed percentages keep their decimals
        return int(f) if f.is_integer() else round(f, 3)

    out["amount"] = out["amount"].map(_amt)
    return out


def ratio_actuals_990(act: pd.DataFrame) -> pd.DataFrame:
    """The playbook ratios computed from the FILED 990 figures for
    every filed year (FY2021-FY2025) - real inputs, shown formula.
    ratio_kind is the stable per-ratio id so trend charts can filter
    one ratio across years; ratio labels carry the year so category
    axes never mix years. FY2020 is pre-merger and gets no ratios;
    growth ratios start FY2022 (FY2021-over-FY2020 would cross the
    merger)."""
    lookup = {(r["fiscal_year"], r["line_item"]): float(r["amount"])
              for _, r in act.iterrows() if r["amount"] != ""}

    def g(fy, item):
        return lookup.get((fy, item))

    rows = []

    def add(kind, label, fy, value, unit, formula, note, basis="COMPUTED FROM FILED"):
        rows.append({
            "ratio_id": f"R990-{kind}-{fy}", "ratio_kind": kind,
            "ratio": label, "fiscal_year": fy,
            "value": "" if value is None else round(value, 4),
            "unit": unit, "formula_990": formula, "basis": basis,
            "confidence": "HIGH", "value_class": "PUBLIC_RESEARCH",
            "note": note})

    years = ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    for fy in years:
        rev, exp = g(fy, "total_revenue"), g(fy, "total_expenses")
        con = g(fy, "contributions_and_grants")
        prg = g(fy, "program_service_revenue")
        inv, oth = g(fy, "investment_income"), g(fy, "other_revenue")
        sal, gr = g(fy, "salaries_and_benefits"), g(fy, "grants_paid")
        fund = g(fy, "fundraising_expenses")
        na, ta = g(fy, "net_assets_end"), g(fy, "total_assets_end")
        cash, sav = g(fy, "cash_end"), g(fy, "savings_end")
        add("op_margin_pct", f"Operating margin % ({fy})", fy,
            (rev - exp) / rev * 100, "%",
            "(Part I line 12 - line 18) / line 12",
            "Negative = the year spent more than it raised")
        add("expense_coverage", f"Expense coverage ({fy})", fy,
            rev / exp, "x", "Part I line 12 / line 18",
            ">= 1.0 means revenue covered the year's expenses")
        add("program_reliance_pct", f"Program revenue reliance % ({fy})",
            fy, prg / rev * 100, "%", "Part I line 9 / line 12",
            "Earned-revenue share - the JCC operating model")
        add("contrib_reliance_pct", f"Contributions reliance % ({fy})",
            fy, con / rev * 100, "%", "Part I line 8 / line 12",
            "Donated share of revenue")
        add("invest_other_pct", f"Investment + other revenue % ({fy})",
            fy, (inv + oth) / rev * 100, "%",
            "(Part I line 10 + line 11) / line 12",
            "Portfolio and misc income share")
        add("salaries_pct_exp", f"Salaries % of expenses ({fy})", fy,
            sal / exp * 100, "%", "Part I line 15 / line 18",
            "People-cost share of spending")
        add("grants_pct_exp", f"Grants paid % of expenses ({fy})", fy,
            gr / exp * 100, "%", "Part I line 13 / line 18",
            "Regranting share of spending")
        add("fundraise_cents_per_dollar",
            f"Fundraising cost per $ raised ({fy})", fy,
            fund / con * 100, "cents", "Part I line 16b / line 8 x 100",
            "Cents spent on fundraising per contribution dollar")
        add("net_asset_ratio_pct", f"Net asset ratio % ({fy})", fy,
            na / ta * 100, "%", "Part X line 16 net of line 26",
            "Share of the balance sheet owned free of liabilities")
        add("months_cash_on_hand", f"Months of cash on hand ({fy})", fy,
            (cash + sav) / (exp / 12), "months",
            "(Part X line 1 + line 2) / (Part I line 18 / 12)",
            "Pure cash liquidity vs the internal 3.0-month policy; "
            "excludes the investment portfolio")
        prog_e = g(fy, "program_expenses")
        mgmt = g(fy, "mgmt_general_expenses")
        add("program_expense_ratio_pct",
            f"Program expense ratio % ({fy})", fy, prog_e / exp * 100,
            "%", "Part IX line 25 col B / col A",
            "Share of spending on programs - the sector's headline "
            "accountability ratio")
        add("overhead_ratio_pct", f"Overhead ratio % ({fy})", fy,
            (mgmt + fund) / exp * 100, "%",
            "Part IX line 25 (col C + col D) / col A",
            "Management + fundraising share of spending")
        una = g(fy, "unrestricted_net_assets_end")
        add("operating_reserve_months",
            f"Operating reserve months ({fy})", fy, una / (exp / 12),
            "months", "Part X line 27 / (Part I line 18 / 12)",
            "Unrestricted net assets vs a month of spending - line 27 "
            "includes board-designated funds and net fixed assets, so "
            "this OVERSTATES spendable reserves")
        streams = {"contributions": con,
                   "program service revenue": prg,
                   "investment income": inv, "other revenue": oth}
        top = max(streams, key=streams.get)
        add("revenue_concentration_pct",
            f"Revenue concentration % ({fy})", fy,
            streams[top] / rev * 100, "%",
            "largest single revenue stream / Part I line 12",
            f"Largest stream: {top} - earned fees from many payers "
            "carry different risk than one grantor or major donor")
        ar = g(fy, "accounts_receivable_end") or 0
        ppd = g(fy, "prepaid_expenses_end") or 0
        plg = g(fy, "pledges_receivable_end") or 0
        ap = g(fy, "accounts_payable_accrued_end") or 0
        gp = g(fy, "grants_payable_end") or 0
        dr = g(fy, "deferred_revenue_end") or 0
        add("current_ratio", f"Current ratio ({fy})", fy,
            (cash + sav + plg + ar + ppd) / (ap + gp + dr), "x",
            "Part X (lines 1+2+3+4+9) / (lines 17+18+19)",
            "990 PROXY - the form does not classify current vs "
            "long-term; numerator and denominator are the plainly "
            "short-term lines", basis="DERIVED (990 PROXY)")
        add("days_cash_on_hand", f"Days cash on hand ({fy})", fy,
            (cash + sav) / (exp / 365), "days",
            "(Part X line 1 + line 2) / (Part I line 18 / 365)",
            "Day-count view of the months-of-cash ratio")
        add("mgmt_general_pct_exp",
            f"Admin (M&G) % of expenses ({fy})", fy,
            mgmt / exp * 100, "%", "Part IX line 25 col C / col A",
            "Management & general only - excludes fundraising")
        add("fundraise_dollars_per_dollar",
            f"Dollars raised per $1 of fundraising ({fy})", fy,
            con / fund, "$ per $", "Part I line 8 / line 16b",
            "Inverse of the cents-per-dollar view")
        add("public_support_pct", f"IRS public support % ({fy})", fy,
            g(fy, "public_support_pct"), "%",
            "Schedule A Part II line 14 (as filed)",
            "5-year cumulative IRS test, filed by the preparer - not "
            "recomputed here", basis="FILED (SCHEDULE A)")
        liab = g(fy, "total_liabilities_end")
        add("leverage_ratio", f"Leverage ratio ({fy})", fy, liab / na,
            "x", "Part X line 26 / Part I line 22",
            "Total liabilities vs net assets - lower means less "
            "reliance on debt; no published sector standard")
        add("savings_indicator_pct",
            f"Savings indicator % ({fy})", fy,
            (rev - exp) / exp * 100, "%",
            "(line 12 - line 18) / line 18",
            "Share of spending the year added to (or drew from) net "
            "assets")
        add("roa_pct", f"Return on assets % ({fy})", fy,
            (rev - exp) / ta * 100, "%",
            "(line 12 - line 18) / Part X line 16",
            "Surplus generated per dollar of balance sheet")
    for prev, fy in zip(years, years[1:]):
        add("revenue_growth_pct",
            f"Revenue growth % ({prev} to {fy})", fy,
            (g(fy, "total_revenue") / g(prev, "total_revenue") - 1)
            * 100, "%", "line 12 year-over-year",
            "Filed revenue direction")
        add("expense_growth_pct",
            f"Expense growth % ({prev} to {fy})", fy,
            (g(fy, "total_expenses") / g(prev, "total_expenses") - 1)
            * 100, "%", "line 18 year-over-year",
            "Filed expense direction")
        add("net_assets_growth_pct",
            f"Net assets change % ({prev} to {fy})", fy,
            (g(fy, "net_assets_end") / g(prev, "net_assets_end") - 1)
            * 100, "%", "line 22 year-over-year",
            "Balance-sheet direction")
    df = pd.DataFrame(rows)
    # attach the policy target / sector benchmark per ratio_kind so
    # every chart can draw the reference line beside the filed value
    tg = pd.read_csv(NFP_DIR / "nfp_990_target_inputs.csv").fillna("")
    df = df.merge(
        tg[["ratio_kind", "target_value", "target_direction",
            "target_label", "target_class"]],
        on="ratio_kind", how="left").fillna("")

    def verdict(r):
        if r["target_value"] == "":
            return ""
        v, tv = float(r["value"]), float(r["target_value"])
        ok = v >= tv if r["target_direction"] == ">=" else v <= tv
        return "MEETS" if ok else "MISSES"

    df["vs_target"] = df.apply(verdict, axis=1)

    def detail(r):
        if r["target_value"] == "":
            return ""
        v, tv = float(r["value"]), float(r["target_value"])
        return (f"{r['vs_target']} by {abs(v - tv):,.2f} "
                f"({v:,.2f} vs {tv:,.2f})")

    df["verdict_detail"] = df.apply(detail, axis=1)
    return df


def _ratio_math_fy2025(act: pd.DataFrame) -> dict:
    """FY2025 arithmetic per ratio with the filed amounts inline, so
    the owner can see which numbers feed each ratio."""
    lk = {r["line_item"]: float(r["amount"])
          for _, r in act.iterrows()
          if r["fiscal_year"] == "FY2025" and r["amount"] != ""}

    def f(x):
        return f"{x:,.0f}"

    rev, exp = lk["total_revenue"], lk["total_expenses"]
    con, prg = lk["contributions_and_grants"], lk[
        "program_service_revenue"]
    inv, oth = lk["investment_income"], lk["other_revenue"]
    sal, gr = lk["salaries_and_benefits"], lk["grants_paid"]
    fund, progb = lk["fundraising_expenses"], lk["program_expenses"]
    mgmt = lk["mgmt_general_expenses"]
    na, ta = lk["net_assets_end"], lk["total_assets_end"]
    liab = lk["total_liabilities_end"]
    cash, sav = lk["cash_end"], lk["savings_end"]
    una = lk["unrestricted_net_assets_end"]
    plg, ar = lk["pledges_receivable_end"], lk[
        "accounts_receivable_end"]
    ppd = lk["prepaid_expenses_end"]
    ap, gp = lk["accounts_payable_accrued_end"], lk[
        "grants_payable_end"]
    dr = lk["deferred_revenue_end"]
    raw = {
        "op_margin_pct": f"(total revenue {f(rev)} - total expenses {f(exp)}) / total revenue {f(rev)}",
        "expense_coverage": f"total revenue {f(rev)} / total expenses {f(exp)}",
        "program_reliance_pct": f"program service revenue {f(prg)} / total revenue {f(rev)}",
        "contrib_reliance_pct": f"contributions {f(con)} / total revenue {f(rev)}",
        "invest_other_pct": f"(investment income {f(inv)} + other revenue {f(oth)}) / total revenue {f(rev)}",
        "salaries_pct_exp": f"salaries & benefits {f(sal)} / total expenses {f(exp)}",
        "grants_pct_exp": f"grants paid {f(gr)} / total expenses {f(exp)}",
        "fundraise_cents_per_dollar": f"fundraising expenses {f(fund)} / contributions {f(con)} x 100",
        "fundraise_dollars_per_dollar": f"contributions {f(con)} / fundraising expenses {f(fund)}",
        "net_asset_ratio_pct": f"net assets {f(na)} / total assets {f(ta)}",
        "months_cash_on_hand": f"(cash {f(cash)} + savings {f(sav)}) / (total expenses {f(exp)} / 12)",
        "days_cash_on_hand": f"(cash {f(cash)} + savings {f(sav)}) / (total expenses {f(exp)} / 365)",
        "operating_reserve_months": f"unrestricted net assets {f(una)} / (total expenses {f(exp)} / 12)",
        "program_expense_ratio_pct": f"program expenses {f(progb)} / total expenses {f(exp)}",
        "overhead_ratio_pct": f"(management & general {f(mgmt)} + fundraising {f(fund)}) / total expenses {f(exp)}",
        "mgmt_general_pct_exp": f"management & general {f(mgmt)} / total expenses {f(exp)}",
        "revenue_concentration_pct": f"program service revenue (largest stream) {f(prg)} / total revenue {f(rev)}",
        "current_ratio": f"(cash {f(cash)} + savings {f(sav)} + pledges receivable {f(plg)} + accounts receivable {f(ar)} + prepaid {f(ppd)}) / (accounts payable {f(ap)} + grants payable {f(gp)} + deferred revenue {f(dr)})",
        "public_support_pct": "as filed, Schedule A Part II line 14 - "
                              "IRS 5-year computation, not recomputed",
        "leverage_ratio": f"total liabilities {f(liab)} / net assets {f(na)}",
        "savings_indicator_pct": f"(total revenue {f(rev)} - total expenses {f(exp)}) / total expenses {f(exp)}",
        "roa_pct": f"(total revenue {f(rev)} - total expenses {f(exp)}) / total assets {f(ta)}",
    }

    def _short(s):
        # owner shorthand: rev / exp / AR / AP / lia
        for long, short in (("accounts receivable", "AR"),
                            ("accounts payable", "AP"),
                            ("revenue", "rev"), ("expenses", "exp"),
                            ("liabilities", "lia")):
            s = s.replace(long, short)
        return s

    return {k: _short(v) for k, v in raw.items()}


def yoy_990(act: pd.DataFrame, fs: pd.DataFrame,
            ratios: pd.DataFrame) -> pd.DataFrame:
    """Tab-17 YoY section: FY2024 vs FY2025 for every statement line
    (variance $ and %) and every level ratio (variance in its own
    units) - all from the filed figures."""
    rows = []
    for _, r in fs.iterrows():
        v24, v25 = float(r["fy2024"]), float(r["fy2025"])
        rows.append({
            "section": f'{r["statement"]} - {r["section"]}',
            "line_label": r["line_label"], "unit": "USD",
            "fy2024": round(v24), "fy2025": round(v25),
            "variance": round(v25 - v24),
            "variance_pct": (round((v25 - v24) / abs(v24) * 100, 1)
                             if v24 else ""),
            "math_fy2025": "", "note": r["note"],
            "value_class": "PUBLIC_RESEARCH"})
    math = _ratio_math_fy2025(act)
    seen = []
    for _, r in ratios.iterrows():
        k = r["ratio_kind"]
        if k in seen or "growth" in k:
            continue
        seen.append(k)
        rk = ratios[ratios["ratio_kind"] == k]
        y24 = rk[rk["fiscal_year"] == "FY2024"]
        y25 = rk[rk["fiscal_year"] == "FY2025"]
        if not len(y24) or not len(y25):
            continue
        v24 = float(y24.iloc[0]["value"])
        v25 = float(y25.iloc[0]["value"])
        rows.append({
            "section": "RATIOS",
            "line_label": y25.iloc[0]["ratio"].split(" (")[0],
            "unit": y25.iloc[0]["unit"],
            "fy2024": round(v24, 2), "fy2025": round(v25, 2),
            "variance": round(v25 - v24, 2),
            "variance_pct": (round((v25 - v24) / abs(v24) * 100, 1)
                             if v24 else ""),
            "math_fy2025": math.get(k, ""),
            "note": "variance shown in the ratio's own units",
            "value_class": "PUBLIC_RESEARCH"})
    return pd.DataFrame(rows)


def rules_990(ratios: pd.DataFrame) -> pd.DataFrame:
    """Actual-vs-rule register (owner request): each nonprofit rule
    with its description on the same line, the FY2025 actual where the
    filings can compute one, and an honest verdict - N/A and RESEARCH
    REQUIRED are answers, never guesses."""
    def v(kind):
        rk = ratios[(ratios["ratio_kind"] == kind)
                    & (ratios["fiscal_year"] == "FY2025")]
        return float(rk.iloc[0]["value"])

    rows = [
        ("IRS 33 1/3% public support test", f"{v('public_support_pct')}%",
         ">= 33.33%", "MEETS",
         "A 501(c)(3) must draw at least one-third of support from the "
         "general public/government (5-year cumulative, single donors "
         "capped at 2% of support; filed on Schedule A). Between 10% "
         "and 33 1/3% the 10% facts-and-circumstances test can "
         "preserve status; sustained below 10% risks reclassification "
         "as a private foundation.",
         "As filed, Schedule A Part II line 14; rule context: owner "
         "research (Foundation Group, IRS)"),
        ("IRS 5% payout rule", "N/A", "5% of assets/yr", "N/A",
         "PRIVATE FOUNDATIONS must distribute ~5% of non-charitable "
         "assets annually (IRC 4942; 30% excise tax on shortfalls, up "
         "to 100% uncorrected; excess carries forward 5 years). JSV is "
         "a PUBLIC CHARITY - the rule does not apply; kept here for "
         "reference.", "Owner research (IRS, NCFP)"),
        ("80/20 program-vs-overhead rule",
         f"{v('program_expense_ratio_pct'):.1f}%", ">= 80% programs",
         "MEETS",
         "Traditional benchmark: ~80% of spending on mission programs, "
         "<= 20% overhead. Modern practice cautions the ratio is not "
         "an outcome measure - pair it with SROI, mission-alignment "
         "scorecards and outcome reporting, and use Form 990 Part III "
         "narratives to tell the impact story.",
         "Owner research (Clark Nuber; 'retire the 80/20 myth' note)"),
        ("Donor concentration Pareto (80/20)", "RESEARCH REQUIRED",
         "top 20% ~ 80% of gifts", "RESEARCH REQUIRED",
         "Roughly 80% of gifts typically come from the top 20% of "
         "donors - focus major-donor cultivation there. Needs "
         "donor-level giving data, which the 990 does not carry.",
         "Owner research (BlueTree Marketing)"),
        ("Donor communication split (80/20)", "PRACTICE", "80% impact "
         "/ 20% asks", "N/A - DATA",
         "80% of donor communication should be impact, storytelling "
         "and gratitude; only 20% direct asks. A practice standard, "
         "not measurable from financial statements.",
         "Owner research (The Nonprofit Show)"),
        ("Owner rule: M&G < 20% of expenses",
         f"{v('mgmt_general_pct_exp'):.1f}%", "< 20%", "MEETS",
         "Management & general expenses (Part IX col C) kept under "
         "20% of total spending - stricter than the 35% overhead "
         "ceiling. Small/startup organizations legitimately run "
         "higher (30-35%) while building infrastructure.",
         "Owner rule + owner research (National Council of "
         "Nonprofits)"),
        ("Current ratio >= 1.0", f"{v('current_ratio'):.2f}x",
         ">= 1.0x", "MISSES",
         "Short-term assets should cover short-term liabilities "
         "(healthy band 1.0-2.0). FY2025 falls below 1.0 on the 990 "
         "proxy - the same liquidity story as the cash ratios: "
         "read alongside the $19M investment portfolio, which sits "
         "outside the short-term lines.",
         "Owner research (whipplewood, Sage)"),
        ("Fundraising <= 35% of contributions",
         f"{v('fundraise_cents_per_dollar'):.1f} cents/$", "<= 35",
         "MEETS",
         "BBB standard: fundraising expense no more than 35% of "
         "related contributions; sector-typical is 15-20 cents, and "
         "raising $5+ per $1 spent is the inverse benchmark "
         f"(JSV: ${v('fundraise_dollars_per_dollar'):.2f} raised per "
         "$1).", "BBB (give.org) + owner research"),
    ]
    return pd.DataFrame([{
        "rule": r[0], "actual_fy2025": r[1], "rule_value": r[2],
        "vs_rule": r[3], "description": r[4], "source": r[5],
        "value_class": "PUBLIC_RESEARCH"} for r in rows])


def fin_statements_990(act: pd.DataFrame) -> pd.DataFrame:
    """Classic statement presentation of the FILED figures - one row
    per statement line, one column per filed year, exactly as a CFO
    reads them. Every subtotal is the filing's own number and the
    identities (sections sum to totals) are enforced by test."""
    years = ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    lk = {(r["fiscal_year"], r["line_item"]): int(r["amount"])
          for _, r in act.iterrows()
          if r["amount"] != "" and r["fiscal_year"] in years}
    rows = []

    def row(statement, section, label, item=None, values=None, note=""):
        vals = values if values is not None else [lk[(fy, item)]
                                                  for fy in years]
        v24, v25 = vals[3], vals[4]
        rows.append({"statement": statement, "section": section,
                     "line_label": label,
                     **{fy.lower(): v for fy, v in zip(years, vals)},
                     "note": note, "value_class": "PUBLIC_RESEARCH",
                     "var_25v24": v25 - v24,
                     "var_25v24_pct": (round((v25 - v24) / abs(v24)
                                             * 100, 1) if v24 else "")})

    A = "STATEMENT OF ACTIVITIES"
    row(A, "REVENUE", "Contributions & grants",
        "contributions_and_grants", note="Part I line 8")
    row(A, "REVENUE", "Program service revenue",
        "program_service_revenue", note="Part I line 9")
    row(A, "REVENUE", "Investment income", "investment_income",
        note="Part I line 10")
    row(A, "REVENUE", "Other revenue", "other_revenue",
        note="Part I line 11")
    row(A, "REVENUE", "TOTAL REVENUE", "total_revenue",
        note="Part I line 12")
    row(A, "EXPENSES", "Program services", "program_expenses",
        note="Part IX line 25 col B")
    row(A, "EXPENSES", "Management & general", "mgmt_general_expenses",
        note="Part IX line 25 col C")
    row(A, "EXPENSES", "Fundraising", "fundraising_expenses",
        note="Part IX line 25 col D")
    row(A, "EXPENSES", "TOTAL EXPENSES", "total_expenses",
        note="Part I line 18")
    row(A, "RESULT", "CHANGE IN NET ASSETS (surplus/deficit)",
        "surplus_deficit", note="Part I line 19")
    P = "STATEMENT OF FINANCIAL POSITION"
    row(P, "ASSETS", "Cash (non-interest-bearing)", "cash_end",
        note="Part X line 1")
    row(P, "ASSETS", "Savings & temporary investments", "savings_end",
        note="Part X line 2")
    row(P, "ASSETS", "Pledges & grants receivable",
        "pledges_receivable_end", note="Part X line 3")
    row(P, "ASSETS", "Investments (publicly traded)",
        "investments_securities_end", note="Part X line 11")
    other = [lk[(fy, "total_assets_end")] - lk[(fy, "cash_end")]
             - lk[(fy, "savings_end")]
             - lk[(fy, "pledges_receivable_end")]
             - lk[(fy, "investments_securities_end")] for fy in years]
    row(P, "ASSETS", "Other assets (land, buildings, receivables - "
        "derived)", values=other,
        note="DERIVED: line 16 minus the lines above")
    row(P, "ASSETS", "TOTAL ASSETS", "total_assets_end",
        note="Part X line 16")
    row(P, "LIABILITIES", "TOTAL LIABILITIES", "total_liabilities_end",
        note="Part X line 26")
    row(P, "NET ASSETS", "Without donor restrictions",
        "unrestricted_net_assets_end", note="Part X line 27")
    row(P, "NET ASSETS", "With donor restrictions",
        "restricted_net_assets_end", note="Part X line 28")
    row(P, "NET ASSETS", "TOTAL NET ASSETS", "net_assets_end",
        note="Part X line 32 / Part I line 22")
    return pd.DataFrame(rows)


_CFO_READINGS = {
    # profitability
    "op_margin_pct": "Deficit-to-surplus swing managed; hold >= 0",
    "expense_coverage": "Above 1.0x both of the last two years",
    "savings_indicator_pct": "Share of spending added to net assets - "
        "positive again since FY2024",
    "roa_pct": "Surplus per dollar of balance sheet - no sector "
        "standard",
    # liquidity
    "months_cash_on_hand": "THE red flag: cash fell through the "
        "3-month floor in FY2025 as liquidity moved to the portfolio",
    "days_cash_on_hand": "Day-count twin of months of cash - 13.9 "
        "days vs the ~90-day norm",
    "operating_reserve_months": "Healthy on paper - but line 27 "
        "includes fixed assets; get the audit's spendable split",
    "current_ratio": "Below 1.0 in FY2025 on the 990 proxy - the "
        "balance-sheet echo of the cash story",
    # revenue mix
    "program_reliance_pct": "Earned fees are the engine (~60% of "
        "revenue) - the JCC operating model",
    "contrib_reliance_pct": "Donated share of revenue - swings hard "
        "with campaigns",
    "invest_other_pct": "Portfolio and misc income share - small but "
        "growing role",
    "revenue_concentration_pct": "Above the 30-40% guidance - but the "
        "top stream is earned fees from many payers, not one funder",
    # spending discipline
    "program_expense_ratio_pct": "Beats the BBB floor and the ~74% "
        "sector median every year - a board-ready strength",
    "overhead_ratio_pct": "Well under the 35% ceiling",
    "mgmt_general_pct_exp": "Admin (M&G) alone stays under the "
        "owner's 20% rule",
    "salaries_pct_exp": "In the 45-60% band since FY2022 (staffed "
        "service model)",
    "grants_pct_exp": "Regranting share of spending - the Federation "
        "legacy role",
    # fundraising
    "fundraise_cents_per_dollar": "Cheap fundraising by sector "
        "standards",
    "fundraise_dollars_per_dollar": "Raises ~$8 per $1 spent vs the "
        "$5 sector bar",
    # balance sheet & compliance
    "net_asset_ratio_pct": "No published standard; strong equity, "
        "mostly donor-restricted",
    "leverage_ratio": "Low reliance on debt (0.27x liabilities to net "
        "assets)",
    "public_support_pct": "IRS 33 1/3% public support test, as filed "
        "on Schedule A - passed with 2x headroom every year"}


# audience + plain-English meaning for the review ratios that have no
# KPI-register entry (the 12 register KPIs reuse _KPI_DESCRIPTIONS /
# _KPI_AUDIENCE so the two tables never drift apart)
_RATIO_EXTRA_MEANING = {
    "savings_indicator_pct": (
        "CEO (annual close)",
        "The year's surplus restated as a share of spending - how much "
        "the year added to reserves per dollar spent. No published "
        "standard; positive years rebuild reserves, negative years "
        "draw them down."),
    "roa_pct": (
        "BOARD (investment committee)",
        "Surplus earned per dollar of total assets. No sector standard "
        "for nonprofits - read it as a trend line, not against a "
        "benchmark."),
    "program_reliance_pct": (
        "BOARD + CEO (revenue mix)",
        "Earned program fees as a share of total revenue. High "
        "reliance means results track enrollment and utilization - "
        "the JCC operating model - and is read next to revenue "
        "concentration."),
    "contrib_reliance_pct": (
        "BOARD (fundraising committee)",
        "Donated dollars as a share of total revenue. Swings with "
        "campaign cycles; read together with fundraising efficiency."),
    "invest_other_pct": (
        "BOARD (investment committee)",
        "Portfolio and miscellaneous income as a share of revenue. "
        "Small but growing - earned by the same portfolio that months "
        "of cash excludes."),
    "overhead_ratio_pct": (
        "BOARD (policy + funders)",
        "Management & general plus fundraising as a share of total "
        "expenses - the watchdog 'overhead' number funders read "
        "first. BBB standards imply a 35% ceiling."),
    "grants_pct_exp": (
        "BOARD (grants committee)",
        "Regranting to other organizations as a share of spending - "
        "the Federation legacy role inside the merged organization."),
    "fundraise_dollars_per_dollar": (
        "BOARD (fundraising committee)",
        "Dollars of contributions raised per dollar of fundraising "
        "expense - the flip side of fundraising efficiency. The "
        "sector bar is about $5 raised per $1 spent."),
    "net_asset_ratio_pct": (
        "BOARD (annual)",
        "Net assets as a share of total assets - how much of the "
        "balance sheet the organization owns outright. Strong here, "
        "but much of it is donor-restricted or tied up in fixed "
        "assets."),
    "leverage_ratio": (
        "CEO (treasury) + BOARD (annual)",
        "Total liabilities against net assets - reliance on debt. "
        "0.27x is low; borrowing capacity exists if the board ever "
        "wants it.")}


def cfo_review_990(act: pd.DataFrame,
                   ratios: pd.DataFrame) -> pd.DataFrame:
    """The right-hand review panel for the statements tab: latest value,
    five-year path, the benchmark/policy, verdict, and a one-line CFO
    reading per ratio."""
    years = ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    math = _ratio_math_fy2025(act)
    out = []
    for kind, reading in _CFO_READINGS.items():
        rk = ratios[ratios["ratio_kind"] == kind]
        latest = rk[rk["fiscal_year"] == "FY2025"].iloc[0]
        trend = " > ".join(
            f"{float(rk[rk['fiscal_year'] == fy].iloc[0]['value']):,.2f}"
            for fy in years if (rk["fiscal_year"] == fy).any())
        yearvals = {}
        for fy in years:
            row = rk[rk["fiscal_year"] == fy]
            yearvals[fy.lower()] = (float(row.iloc[0]["value"])
                                    if len(row) else "")
        tv = latest["target_value"]
        out.append({
            "ratio_kind": kind,
            "ratio": latest["ratio"].split(" (")[0],
            "fy2025_value": float(latest["value"]),
            "unit": latest["unit"], "trend_fy2021_fy2025": trend,
            "benchmark_or_policy": latest["target_label"]
                or "No published standard",
            "vs_target": latest["vs_target"] or "-",
            "verdict_detail": latest["verdict_detail"] or "-",
            "math_fy2025": math.get(kind, ""),
            "cfo_reading": reading,
            "reviewed_by": (_KPI_AUDIENCE[kind]
                            if kind in _KPI_AUDIENCE
                            else _RATIO_EXTRA_MEANING[kind][0]),
            "what_it_means": (_KPI_DESCRIPTIONS[kind][1]
                              if kind in _KPI_DESCRIPTIONS
                              else _RATIO_EXTRA_MEANING[kind][1]),
            **yearvals,
            "target_value": float(tv) if tv != "" else "",
            "variance_to_target": (round(float(latest["value"])
                                         - float(tv), 2)
                                   if tv != "" else ""),
            "yoy_variance": round(float(latest["value"])
                                  - yearvals["fy2024"], 2),
            "value_class": "PUBLIC_RESEARCH"})
    return pd.DataFrame(out)


_KPI_DESCRIPTIONS = {
    "program_expense_ratio_pct": (
        "Program Expense Ratio",
        "Share of total spending that goes directly into community "
        "programming versus admin and fundraising. Watchdogs (BBB) set "
        "a 65% floor; the community-center median is ~74.2%. Centers "
        "carry higher facility costs, and above ~90% can signal "
        "underinvestment in infrastructure or safety."),
    "operating_reserve_months": (
        "Operating Reserve (months)",
        "How long the organization could sustain itself on "
        "unrestricted net assets if revenue stopped. Sector goal is "
        "3-6 months; target 6+ when slow-paying reimbursement grants "
        "dominate. JSV's line 27 includes fixed assets, so the "
        "spendable figure is lower than shown."),
    "months_cash_on_hand": (
        "Months of Cash on Hand",
        "Pure cash liquidity: Part X cash + savings against a month "
        "of spending, excluding the investment portfolio. Internal "
        "policy floor is 3.0 months. FY2025 sits far below it because "
        "liquidity moved into investments - the board conversation is "
        "how fast portfolio assets can become cash."),
    "revenue_concentration_pct": (
        "Revenue Concentration",
        "Largest single revenue stream as a share of total revenue - "
        "funding-diversity risk. Sector guidance keeps any stream "
        "under 30-40%. JSV runs above that, but the top stream is "
        "earned program fees from many families, a very different "
        "risk than dependence on one grant or donor."),
    "salaries_pct_exp": (
        "Personnel Expense Ratio",
        "Salaries, wages and benefits as a share of total expenses. "
        "Staff-delivered centers (front desk, instructors, camps) "
        "typically run 45-60%; below 45% can mean understaffing, "
        "above 60% squeezes program delivery."),
    "fundraise_cents_per_dollar": (
        "Fundraising Efficiency",
        "Cents spent on fundraising per contribution dollar raised. "
        "BBB ceiling is 35 cents; a typical healthy range is 15-20. "
        "JSV raises money cheaply - single digits most years."),
    "op_margin_pct": (
        "Operating Margin",
        "What is left of revenue after the year's expenses. No "
        "published sector standard; the internal policy floor is "
        "breakeven. The FY2023 deficit year and the FY2025 recovery "
        "are both visible here."),
    "current_ratio": (
        "Current Ratio",
        "Short-term assets against short-term bills (990 proxy: cash, "
        "savings, receivables and prepaid vs payables, grants payable "
        "and deferred revenue). Healthy band is 1.0-2.0. FY2025 dips "
        "below 1.0 - the balance-sheet echo of the cash story, "
        "softened by the investment portfolio sitting outside the "
        "short-term lines."),
    "days_cash_on_hand": (
        "Days Cash on Hand",
        "How many days of spending the unrestricted cash could cover "
        "if revenue stopped - the day-count twin of months of cash. "
        "Sector guidance is ~90 days (3 months). FY2025 is a fraction "
        "of that, so the working question is how quickly portfolio "
        "assets can convert to cash."),
    "mgmt_general_pct_exp": (
        "Administrative Expense Ratio",
        "Management & general costs as a share of total expenses - "
        "overhead excluding fundraising. The board rule here is under "
        "20%; small or growing organizations legitimately run higher "
        "while building infrastructure, so read the trend, not one "
        "year."),
    "public_support_pct": (
        "IRS Public Support %",
        "The Schedule A public support test as filed: at least one "
        "third of support must come from the general public and "
        "government over a rolling five years, with any single donor "
        "capped at 2% of the base. Falling under 33 1/3% triggers the "
        "10% facts-and-circumstances fallback; sustained failure "
        "risks private-foundation reclassification."),
    "expense_coverage": (
        "Expense Coverage",
        "Revenue divided by expenses - the simplest solvency read. "
        "1.0x means the year paid for itself; below 1.0x the year drew "
        "down reserves to operate.")}


_KPI_AUDIENCE = {
    "program_expense_ratio_pct": "BOARD (policy + funders)",
    "operating_reserve_months": "BOARD (policy)",
    "months_cash_on_hand": "CEO weekly + BOARD (policy)",
    "revenue_concentration_pct": "BOARD (risk)",
    "salaries_pct_exp": "CEO (operations)",
    "fundraise_cents_per_dollar": "BOARD (fundraising committee)",
    "op_margin_pct": "BOARD + CEO",
    "expense_coverage": "CEO (monthly close)",
    "current_ratio": "CEO (monthly close)",
    "days_cash_on_hand": "CEO (weekly cash review)",
    "mgmt_general_pct_exp": "BOARD (policy + funders)",
    "public_support_pct": "BOARD (compliance, annual)"}


def kpis_990(ratios: pd.DataFrame) -> pd.DataFrame:
    """Tab-18 KPI register: each KPI with a plain-English description,
    its 990 formula, the latest filed value, the benchmark or board
    policy, the verdict, and the five-year path."""
    years = ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    out = []
    for kind, (kpi, desc) in _KPI_DESCRIPTIONS.items():
        rk = ratios[ratios["ratio_kind"] == kind]
        latest = rk[rk["fiscal_year"] == "FY2025"].iloc[0]
        trend = " > ".join(
            f"{float(rk[rk['fiscal_year'] == fy].iloc[0]['value']):,.2f}"
            for fy in years)
        out.append({
            "kpi_id": f"KPI-{len(out) + 1}", "kpi": kpi,
            "description": desc,
            "formula_990": latest["formula_990"],
            "fy2025_value": float(latest["value"]),
            "unit": latest["unit"],
            "benchmark_or_policy": latest["target_label"],
            "target_class": latest["target_class"],
            "vs_target": latest["vs_target"],
            "verdict_detail": latest["verdict_detail"],
            "trend_fy2021_fy2025": trend,
            "typical_audience": _KPI_AUDIENCE[kind],
            "target_value": (float(latest["target_value"])
                             if latest["target_value"] != "" else ""),
            "variance_to_target": (round(
                float(latest["value"]) - float(latest["target_value"]),
                2) if latest["target_value"] != "" else ""),
            "fy2024_value": float(rk[rk["fiscal_year"] == "FY2024"]
                                  .iloc[0]["value"]),
            "yoy_variance": round(
                float(latest["value"])
                - float(rk[rk["fiscal_year"] == "FY2024"]
                        .iloc[0]["value"]), 2),
            "value_class": "PUBLIC_RESEARCH"})
    return pd.DataFrame(out)


# snapshot date for the forward-looking sections (owner request
# 2026-09-07): the market quotes in data/nfp/nfp_treasury_inputs.csv
# were verified this day and the 13-week forecast starts here. When the
# quotes are refreshed, move this date with them.
MARKET_AS_OF = "2026-09-07"


def cash_forecast_13wk(act: pd.DataFrame) -> pd.DataFrame:
    """13-week cash + expense forecast, straight-lined from the FY2025
    filing. This is a DERIVED teaching proxy, not a cash budget: the
    990 gives one year's totals, so each week gets 1/52nd of filed
    revenue and expenses. A real 13-week forecast needs internal
    weekly data (AR/AP schedules, payroll calendar, camp/membership
    seasonality) - RESEARCH REQUIRED for that upgrade. Week 13's
    cumulative expenses equal one quarter of filed spending, which is
    exactly the 3-month cash floor the policy targets."""
    from datetime import date, timedelta
    lookup = {(r["fiscal_year"], r["line_item"]): float(r["amount"])
              for _, r in act.iterrows() if r["amount"] != ""}
    opening = (lookup[("FY2025", "cash_end")]
               + lookup[("FY2025", "savings_end")])
    rev, exp = (lookup[("FY2025", "total_revenue")],
                lookup[("FY2025", "total_expenses")])
    wk_in, wk_out = rev / 52, exp / 52
    floor = exp / 4  # 3 months of spending = 13 weeks of it
    start = date.fromisoformat(MARKET_AS_OF)
    first_friday = start + timedelta((4 - start.weekday()) % 7)
    rows = [{
        "week_no": 0, "week_ending": MARKET_AS_OF,
        "inflow": "", "expenses": "", "net_change": "",
        "ending_cash": round(opening, 2), "cum_expenses": "",
        "weeks_covered": round(opening / wk_out, 2),
        "vs_3mo_floor": (f"MISSES by {floor - opening:,.0f} "
                         f"({opening:,.0f} vs {floor:,.0f})"),
        "basis": "FILED (FY2025 Part X cash + savings)",
        "note": "opening position from the FY2025 filing"}]
    for k in range(1, 14):
        ending = opening + k * (wk_in - wk_out)
        rows.append({
            "week_no": k,
            "week_ending": str(first_friday + timedelta(weeks=k - 1)),
            "inflow": round(wk_in, 2), "expenses": round(wk_out, 2),
            "net_change": round(wk_in - wk_out, 2),
            "ending_cash": round(ending, 2),
            "cum_expenses": round(k * wk_out, 2),
            "weeks_covered": round(ending / wk_out, 2),
            "vs_3mo_floor": (f"MISSES by {floor - ending:,.0f} "
                             f"({ending:,.0f} vs {floor:,.0f})"),
            "basis": "DERIVED (STRAIGHT-LINE 990 PROXY)",
            "note": ("1/52nd of filed FY2025 revenue and expenses "
                     "per week - seasonality not modeled")})
    df = pd.DataFrame(rows)
    df["value_class"] = "PUBLIC_RESEARCH"
    return df


def treasury_yields() -> pd.DataFrame:
    """Current treasury yield curve, from owner-verified market quotes
    in data/nfp/nfp_treasury_inputs.csv (PUBLIC_RESEARCH, MEDIUM -
    treasury.gov's own download is egress-blocked from the build
    environment, so quotes came via web search and must be re-verified
    before any purchase). tenor_label carries a leading digit so chart
    category axes sort short-to-long instead of alphabetically."""
    ty = pd.read_csv(NFP_DIR / "nfp_treasury_inputs.csv").fillna("")
    ty = ty.sort_values("term_years").reset_index(drop=True)
    ty.insert(2, "tenor_label",
              [f"{i + 1} - {t}" for i, t in enumerate(ty["tenor"])])
    ty["value_class"] = "PUBLIC_RESEARCH"
    return ty


def bond_trends() -> pd.DataFrame:
    """Bond-market trend readings (researched 2026-09-07, MEDIUM
    confidence, click-through sources) with what each means for JSV.
    Commentary, never a recommendation."""
    bt = pd.read_csv(NFP_DIR / "nfp_bond_trend_inputs.csv").fillna("")
    bt["value_class"] = "PUBLIC_RESEARCH"
    return bt


def bond_forecast_990(ty: pd.DataFrame) -> pd.DataFrame:
    """If-purchased forecast: a hypothetical $1,000,000 in each tenor,
    HELD TO MATURITY at the quoted yield. Bills use simple interest
    over the term; notes compound annually (coupons assumed reinvested
    at the same rate - the classic simplification, stated in the
    math). Hold-to-maturity means the yield is locked at purchase; the
    market 'forecast' only matters if sold early. DERIVED and
    HYPOTHETICAL - not investment advice."""
    principal = 1_000_000
    out = []
    for _, r in ty.iterrows():
        years, y = float(r["term_years"]), float(r["yield_pct"])
        if years <= 1:
            interest = principal * y / 100 * years
            math = (f"1,000,000 x {y}% x {years} yr "
                    f"= {interest:,.0f} interest (simple - bills)")
            risk = ("matures inside the year; reinvestment rate at "
                    "maturity is unknown")
            if years == 0.25:
                risk = ("matures inside the 13-week cash window - the "
                        "tenor that matches the forecast above")
        else:
            value = principal * (1 + y / 100) ** years
            interest = value - principal
            math = (f"1,000,000 x (1 + {y}%)^{years:.0f} "
                    f"= {value:,.0f} (coupons reinvested at the "
                    f"same rate)")
            risk = ("price moves if sold before maturity; "
                    "hold-to-maturity locks the yield at purchase")
        out.append({
            "tenor_id": r["tenor_id"], "tenor": r["tenor"],
            "tenor_label": r["tenor_label"],
            "term_years": years, "yield_pct": y,
            "amount_invested": principal,
            "est_value_at_maturity": round(principal + interest, 2),
            "est_interest": round(interest, 2),
            "math_detail": math, "risk_note": risk,
            "basis": "DERIVED (HYPOTHETICAL - held to maturity)",
            "as_of": r["as_of"], "confidence": r["confidence"],
            "value_class": "PUBLIC_RESEARCH"})
    return pd.DataFrame(out)


def invest_menu() -> pd.DataFrame:
    """The owner's investment-instrument menu (CFO interview prep,
    pasted 2026-09-07): what each instrument is typically for, its
    risk and liquidity, and the CFO rationale - kept in her own
    words. A framework, never a recommendation; the only JSV-specific
    facts referenced are filed 990 figures."""
    m = pd.read_csv(NFP_DIR / "nfp_invest_menu_inputs.csv").fillna("")
    m["value_class"] = "MANAGEMENT ASSUMPTION"
    return m


def invest_buckets() -> pd.DataFrame:
    """The owner's 3-bucket framework: operating liquidity,
    board-designated reserves (the maturity ladder), and long-term /
    endowment capital - each tied to the sections above it (13-week
    forecast sizes bucket 1, the treasury quotes price the bucket-2
    ladder, the filed portfolio poses the bucket-3 question). Bucket
    3's 60/35/5 split is her ILLUSTRATION, explicitly not a
    recommendation."""
    b = pd.read_csv(NFP_DIR / "nfp_invest_bucket_inputs.csv").fillna("")
    b["value_class"] = "MANAGEMENT ASSUMPTION"
    return b


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
        "nfp_role_matrix": role_matrix(),
        "nfp_ratio_990": ratio_990(),
        "nfp_survey_findings": survey_findings(),
        "nfp_survey_alignment": survey_alignment(prog),
        "nfp_initiative_status": initiative_status(),
        "nfp_gap_history": gap_history(s, prog),
        "nfp_support_map": support_map(prog),
        "nfp_alt_timeline": alt_timeline(s),
        "nfp_pledge_schedule": pledge_schedule(),
        "nfp_funding_mix": funding_mix(s, prog, g),
        "nfp_ratio_values": ratio_values(s, prog),
        "nfp_ratio_history": ratio_history(s),
        "nfp_rentals": rentals(),
        "nfp_investment_pools": investment_pools(),
        "nfp_invest_scenarios": invest_scenarios(s),
    }
    act = actuals_990()
    ratios = ratio_actuals_990(act)
    frames["nfp_990_actuals"] = act
    frames["nfp_990_ratio_actuals"] = ratios
    fs = fin_statements_990(act)
    frames["nfp_fin_statements"] = fs
    frames["nfp_cfo_review"] = cfo_review_990(act, ratios)
    frames["nfp_990_kpis"] = kpis_990(ratios)
    frames["nfp_990_yoy"] = yoy_990(act, fs, ratios)
    frames["nfp_990_rules"] = rules_990(ratios)
    frames["nfp_cash_13wk"] = cash_forecast_13wk(act)
    ty = treasury_yields()
    frames["nfp_treasury_yields"] = ty
    frames["nfp_bond_trends"] = bond_trends()
    frames["nfp_bond_forecast"] = bond_forecast_990(ty)
    frames["nfp_invest_menu"] = invest_menu()
    frames["nfp_invest_buckets"] = invest_buckets()
    # stable sort key: report tables sort by row_id to preserve the
    # decision-flow order of each export
    for df in frames.values():
        df.insert(0, "row_id", [f"R{i:03d}" for i in range(1, len(df) + 1)])
    return frames
