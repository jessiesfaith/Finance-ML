"""
NFP ML layer — the eleven owner-approved models (§55), honestly built.

Ground rules from the master prompt, enforced here and by tests:
  * §54 deterministic-first: NOTHING in this module touches the
    deterministic exports. The nfp_* tables are byte-identical with or
    without the ML build (tests prove it).
  * Every output row is value_class = "MODEL ESTIMATE" and is shown
    NEXT TO the management assumption it parallels — it never replaces
    an ACTUAL, HISTORICAL, MANAGEMENT ASSUMPTION, or PUBLIC_RESEARCH
    value.
  * Training data is a labeled SYNTHETIC monthly history (seed 777,
    generator below, committed like the market history). At client
    cutover the same models retrain on real data; the code does not
    change.

Models (owner approved 2026-09-05):
  1  Program Participation Forecast   linear trend + month-of-year
  2  Donation Forecast                linear trend + month-of-year
  3  Grant Renewal Probability        logistic regression on renewal
                                      history features
  4  Program Cost Forecast            linear trend + month-of-year
  5  Cash Flow Forecast               linear trend + month-of-year
  6  Risk Scoring                     historical adverse-month frequency
  7  Scenario Probability             regime frequencies of net-cash
                                      residuals
  8  Anomaly Detection                z-score of deseasonalized
                                      residuals (|z| > 3)
  9  Donor Retention                  trend on cohort retention rates
  10 Pledge Collection                logistic regression on payment
                                      history
  11 Capacity Forecasting             months-to-capacity from the
                                      participation slope

Everything is small, explainable, and deterministic (fixed seed, fixed
solver): rerunning the build reproduces every number exactly.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
NFP_DIR = ROOT / "data" / "nfp"
REPORTS = ROOT / "reports"

SEED = 777
N_MONTHS = 48                       # 2022-10 .. 2026-09
SOURCE_REF = f"financials/nfp_ml.py seed {SEED}"
ANOMALY_MONTH = "2025-03"           # injected one-off special campaign
FORECAST_MONTHS = 12
ESTIMATE = "MODEL ESTIMATE"


def _months() -> list[str]:
    return [f"{2022 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}"
            for i in range(N_MONTHS)]


def _future_months() -> list[str]:
    return [f"{2026 + (9 + i) // 12}-{(9 + i) % 12 + 1:02d}"
            for i in range(FORECAST_MONTHS)]


# ----------------------------------------------------------------------
# synthetic training history (committed; regenerable byte-identically)
# ----------------------------------------------------------------------

def regen_history() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    months = _months()
    t = np.arange(N_MONTHS)
    moy = np.array([int(m[5:]) for m in months])          # 1..12
    prog = pd.read_csv(NFP_DIR / "nfp_program_inputs.csv")

    series_rows = []

    def add_series(series_id, values):
        for m, v in zip(months, values):
            series_rows.append({"series_id": series_id, "month": m,
                                "value": round(float(v), 2),
                                "value_class": "SYNTHETIC",
                                "source_reference": SOURCE_REF})

    for _, p in prog.iterrows():
        # participants: grow from ~82% of today's level, Sep/Jan bumps
        base = p["participants"]
        level = base * (0.82 + 0.18 * t / (N_MONTHS - 1))
        seasonal = 1 + 0.06 * np.isin(moy, [9, 1])
        noise = rng.normal(1, 0.03, N_MONTHS)
        add_series(f"participants:{p['program_id']}",
                   level * seasonal * noise)
        # monthly cost: today's annual cost / 12 with mild drift + noise
        cost_base = (p["personnel"] + p["direct_costs"]
                     + p["allocated_overhead"]) / 12.0
        cost = cost_base * (0.90 + 0.10 * t / (N_MONTHS - 1))
        add_series(f"cost:{p['program_id']}",
                   cost * rng.normal(1, 0.02, N_MONTHS))

    # organization-level unrestricted donations: December spike is
    # SEASONAL; 2025-03 carries a one-off special campaign (the anomaly
    # the detector must find)
    don_base = 95000 * (0.88 + 0.12 * t / (N_MONTHS - 1))
    don = don_base * np.where(moy == 12, 2.2, 1.0)
    don = don * rng.normal(1, 0.05, N_MONTHS)
    don[months.index(ANOMALY_MONTH)] *= 2.5
    add_series("donations:ORG", don)

    # org monthly net cash: funding - cost with noise
    net = rng.normal(9000, 22000, N_MONTHS) + 40 * t
    add_series("net_cash:ORG", net)

    history = pd.DataFrame(series_rows,
                           columns=["series_id", "month", "value",
                                    "value_class", "source_reference"])

    # donor cohorts: yearly prior donors + renewals (retention drifts
    # down slightly - the model should surface that)
    cohort_rows = []
    prior = 1450.0
    for i, year in enumerate(range(2022, 2026)):
        retention = 0.76 - 0.015 * i + rng.normal(0, 0.008)
        renewing = round(prior * retention)
        cohort_rows.append({"year": year, "prior_year_donors": int(prior),
                            "renewing_donors": int(renewing),
                            "retention_rate": round(renewing / prior, 4),
                            "value_class": "SYNTHETIC",
                            "source_reference": SOURCE_REF})
        prior = renewing + rng.integers(300, 420)
    donors = pd.DataFrame(cohort_rows)

    # grant renewal history: 60 grant-years with a known true mechanism
    gr_rows = []
    for i in range(60):
        years_funded = int(rng.integers(1, 10))
        funder_type = ["Foundation", "Government", "Corporate"][
            int(rng.integers(0, 3))]
        reported_on_time = int(rng.random() < 0.85)
        logit = (-0.9 + 0.28 * years_funded + 0.9 * reported_on_time
                 + (0.5 if funder_type == "Government" else 0.0))
        renewed = int(rng.random() < 1 / (1 + np.exp(-logit)))
        gr_rows.append({"obs_id": f"GR-{i + 1:03d}",
                        "years_funded": years_funded,
                        "funder_type": funder_type,
                        "reported_on_time": reported_on_time,
                        "renewed": renewed, "value_class": "SYNTHETIC",
                        "source_reference": SOURCE_REF})
    grant_hist = pd.DataFrame(gr_rows)

    # pledge installment history: 80 installments
    pl_rows = []
    for i in range(80):
        signed = int(rng.random() < 0.75)
        large = int(rng.random() < 0.4)
        logit = -0.3 + 1.6 * signed + 0.4 * large
        paid_on_time = int(rng.random() < 1 / (1 + np.exp(-logit)))
        pl_rows.append({"obs_id": f"PI-{i + 1:03d}", "signed": signed,
                        "large_gift": large,
                        "paid_within_90d": paid_on_time,
                        "value_class": "SYNTHETIC",
                        "source_reference": SOURCE_REF})
    pledge_hist = pd.DataFrame(pl_rows)

    return {"nfp_history": history, "nfp_donor_cohorts": donors,
            "nfp_grant_renewal_history": grant_hist,
            "nfp_pledge_payment_history": pledge_hist}


def load_history() -> dict[str, pd.DataFrame]:
    return {name: pd.read_csv(NFP_DIR / f"{name}.csv")
            for name in ["nfp_history", "nfp_donor_cohorts",
                         "nfp_grant_renewal_history",
                         "nfp_pledge_payment_history"]}


# ----------------------------------------------------------------------
# model helpers
# ----------------------------------------------------------------------

def _trend_forecast(series: pd.DataFrame):
    """LinearRegression on time index + month-of-year dummies.
    Returns (fitted values, 12-month forecast, monthly slope)."""
    y = series["value"].to_numpy()
    t = np.arange(len(y))
    moy = np.array([int(m[5:]) for m in series["month"]])
    X = np.column_stack([t] + [(moy == k).astype(float)
                               for k in range(2, 13)])
    model = LinearRegression().fit(X, y)
    fitted = model.predict(X)
    ft = np.arange(len(y), len(y) + FORECAST_MONTHS)
    fmoy = np.array([int(m[5:]) for m in _future_months()])
    FX = np.column_stack([ft] + [(fmoy == k).astype(float)
                                 for k in range(2, 13)])
    return fitted, model.predict(FX), float(model.coef_[0])


def _logistic(X: np.ndarray, y: np.ndarray) -> LogisticRegression:
    return LogisticRegression(solver="lbfgs", max_iter=1000).fit(X, y)


def fit_grant_model(gh: pd.DataFrame) -> LogisticRegression:
    """Model 3 features: years funded, on-time reporting, funder type."""
    X = np.column_stack([
        gh["years_funded"], gh["reported_on_time"],
        (gh["funder_type"] == "Government").astype(int),
        (gh["funder_type"] == "Corporate").astype(int)])
    return _logistic(X, gh["renewed"].to_numpy())


def fit_pledge_model(ph: pd.DataFrame) -> LogisticRegression:
    """Model 10 features: signed (vs verbal), large gift."""
    Xp = np.column_stack([ph["signed"], ph["large_gift"]])
    return _logistic(Xp, ph["paid_within_90d"].to_numpy())


# ----------------------------------------------------------------------
# the eleven models
# ----------------------------------------------------------------------

def build_ml() -> dict[str, pd.DataFrame]:
    hist = load_history()
    h = hist["nfp_history"]
    prog = pd.read_csv(NFP_DIR / "nfp_program_inputs.csv")
    grants = pd.read_csv(NFP_DIR / "nfp_grant_inputs.csv")
    pledges = pd.read_csv(NFP_DIR / "nfp_pledge_inputs.csv")
    risks = pd.read_csv(NFP_DIR / "nfp_risk_inputs.csv")

    series_out, estimates, anomalies = [], [], []

    def estimate(model, target, value, unit, management, basis, caveat):
        estimates.append({
            "model": model, "target": target, "estimate": value,
            "unit": unit, "management_comparison": management,
            "basis": basis, "caveat": caveat, "value_class": ESTIMATE})

    def emit_series(series_id, label):
        s = h[h["series_id"] == series_id].reset_index(drop=True)
        fitted, forecast, slope = _trend_forecast(s)
        category = series_id.split(":")[0]
        for m, v in zip(s["month"], s["value"]):
            series_out.append({"series_id": series_id, "series": label,
                               "category": category,
                               "month": m, "history_value": v,
                               "estimate_value": "",
                               "kind": "HISTORY (SYNTHETIC)",
                               "value_class": "SYNTHETIC"})
        for m, v in zip(_future_months(), forecast):
            series_out.append({"series_id": series_id, "series": label,
                               "category": category,
                               "month": m, "history_value": "",
                               "estimate_value": round(float(v), 2),
                               "kind": ESTIMATE,
                               "value_class": ESTIMATE})
        return s, fitted, forecast, slope

    # 1 + 11: participation forecast & capacity forecasting
    for _, p in prog.iterrows():
        sid = f"participants:{p['program_id']}"
        s, fitted, forecast, slope = emit_series(
            sid, f"{p['program_name']} participants")
        year_ahead = float(forecast[-1])
        estimate("1 Program Participation Forecast",
                 f"{p['program_name']} participants in 12 months",
                 round(year_ahead, 0), "participants",
                 f"current actual {p['participants']}",
                 f"linear trend + month-of-year on {len(s)} months "
                 f"(slope {slope:+.2f}/month)",
                 "Trained on SYNTHETIC history; retrain at cutover")
        if slope > 0.01:
            months_to_cap = (p["capacity"] - p["participants"]) / slope
            cap_text = (f"{months_to_cap:,.0f} months"
                        if months_to_cap > 0 else "at/over capacity now")
        else:
            cap_text = "not within horizon (flat/declining trend)"
        estimate("11 Capacity Forecasting",
                 f"{p['program_name']} months until capacity "
                 f"({p['capacity']})", cap_text, "months",
                 f"utilization today "
                 f"{100 * p['participants'] / p['capacity']:.0f}%",
                 "capacity minus current participants over the modeled "
                 "monthly slope",
                 "Linear extrapolation only - demand shifts break it")

    # 4: program cost forecast
    for _, p in prog.iterrows():
        sid = f"cost:{p['program_id']}"
        s, fitted, forecast, slope = emit_series(
            sid, f"{p['program_name']} monthly cost")
        estimate("4 Program Cost Forecast",
                 f"{p['program_name']} annual cost next 12 months",
                 round(float(forecast.sum()), 0), "USD",
                 f"current annual cost "
                 f"{p['personnel'] + p['direct_costs'] + p['allocated_overhead']:,.0f}",
                 f"linear trend + seasonality on {len(s)} months",
                 "Cost drivers (headcount, vendors) are not modeled "
                 "separately yet")

    # 2: donation forecast (+ 8: anomaly detection on the same series)
    s, fitted, forecast, slope = emit_series("donations:ORG",
                                             "Org unrestricted donations")
    estimate("2 Donation Forecast",
             "Unrestricted donations next 12 months",
             round(float(forecast.sum()), 0), "USD",
             "management plan lives in nfp_settings "
             "(org_campaign_other_revenue)",
             f"linear trend + month-of-year on {len(s)} months; December "
             "seasonality learned from history",
             "One-off campaigns are exactly what this misses - see the "
             "anomaly model")
    resid = s["value"].to_numpy() - fitted
    z = (resid - resid.mean()) / resid.std()
    for m, val, zi in zip(s["month"], s["value"], z):
        if abs(zi) > 3:
            anomalies.append({
                "series": "Org unrestricted donations", "month": m,
                "value": round(float(val), 0), "z_score": round(float(zi), 2),
                "note": "One-off spike vs trend+seasonality - matches the "
                        "seeded special campaign" if m == ANOMALY_MONTH
                        else "Outlier vs trend+seasonality",
                "value_class": ESTIMATE})

    # 5: cash flow forecast
    s, fitted, forecast, slope = emit_series("net_cash:ORG",
                                             "Org monthly net cash")
    estimate("5 Cash Flow Forecast",
             "Net operating cash next 12 months",
             round(float(forecast.sum()), 0), "USD",
             "deterministic BASE scenario gap on tab 9",
             f"linear trend + month-of-year on {len(s)} months",
             "Excludes campaign/project cash - those live on tab 11")

    # 7: scenario probability from net-cash residual regimes
    resid = s["value"].to_numpy() - fitted
    q20, q80 = np.quantile(resid, [0.2, 0.8])
    stress_line = resid.mean() - 2 * resid.std()
    n = len(resid)
    p_stress = float((resid < stress_line).sum()) / n
    p_down = float(((resid >= stress_line) & (resid < q20)).sum()) / n
    p_up = float((resid > q80).sum()) / n
    p_base = 1.0 - p_stress - p_down - p_up
    for name, pr in [("BASE", p_base), ("UPSIDE", p_up),
                     ("DOWNSIDE", p_down), ("STRESS", p_stress)]:
        estimate("7 Scenario Probability",
                 f"P({name}) from history",
                 round(pr, 3), "probability",
                 "deterministic scenarios carry no probabilities "
                 "(management judgment)",
                 f"frequency of net-cash residual regimes over {n} months",
                 "Backward-looking frequencies, not forward guarantees")

    # 3: grant renewal probability
    gh = hist["nfp_grant_renewal_history"]
    m3 = fit_grant_model(gh)
    live = grants[grants["status"].isin(["CURRENT", "RENEWAL"])
                  & grants["amount"].notna()]
    for _, g in live.iterrows():
        feats = np.array([[3, 1,
                           int(g["funder_type"] == "Government"),
                           int(g["funder_type"] == "Corporate")]])
        p_model = float(m3.predict_proba(feats)[0, 1])
        estimate("3 Grant Renewal Probability",
                 f"{g['grant_name']} ({g['funder']})",
                 round(p_model, 3), "probability",
                 f"management estimate "
                 f"{g['renewal_probability_pct']:.0f}% (ASSUMPTION)",
                 f"logistic regression on {len(gh)} grant-year "
                 "observations (years funded, on-time reporting, "
                 "funder type); assumed features: 3 years funded, "
                 "reporting on time",
                 "Trained on SYNTHETIC history; the deterministic "
                 "engine keeps using the management estimate")

    # 9: donor retention
    dc = hist["nfp_donor_cohorts"]
    Xr = dc["year"].to_numpy().reshape(-1, 1).astype(float)
    m9 = LinearRegression().fit(Xr, dc["retention_rate"])
    next_rate = float(m9.predict(np.array([[2026.0]]))[0])
    estimate("9 Donor Retention",
             "2026 donor retention rate",
             round(next_rate, 3), "rate",
             f"latest cohort actual {dc['retention_rate'].iloc[-1]:.3f}",
             f"linear trend on {len(dc)} yearly cohorts "
             f"(drifting {m9.coef_[0] * 100:+.1f}pts/yr)",
             "A retention program would break this trend - that is "
             "the point of seeing it")

    # 10: pledge collection
    ph = hist["nfp_pledge_payment_history"]
    m10 = fit_pledge_model(ph)
    for _, pl in pledges.iterrows():
        feats = np.array([[int(pl["signed"] == "SIGNED"),
                           int(pl["amount"] >= 200000)]])
        p_model = float(m10.predict_proba(feats)[0, 1])
        estimate("10 Pledge Collection",
                 f"{pl['donor']} (${pl['amount']:,.0f}, {pl['signed']})",
                 round(p_model, 3), "probability",
                 f"management estimate "
                 f"{pl['collection_probability_pct']:.0f}% (ASSUMPTION)",
                 f"logistic regression on {len(ph)} historical "
                 "installments (signed vs verbal, gift size)",
                 "Installment-level model; timing model is Phase 2")

    # 6: risk scoring from monitored series
    don_s = h[h["series_id"] == "donations:ORG"].reset_index(drop=True)
    don_fit, _, _ = _trend_forecast(don_s)
    adverse = float((don_s["value"].to_numpy() < 0.85 * don_fit).mean())
    for risk_name, freq, mgmt in [
        ("Donor concentration / donation shortfall months", adverse,
         "management likelihood "
         + str(int(risks.loc[risks['risk_id'] == 'RK-2',
                             'likelihood'].iloc[0])) + "/5"),
    ]:
        estimate("6 Risk Scoring", risk_name,
                 round(freq, 3), "frequency of adverse months",
                 mgmt,
                 "share of months where donations ran below 85% of "
                 "trend+seasonality",
                 "Only donation risk is instrumented so far; other "
                 "risks keep management scores")

    series_df = pd.DataFrame(series_out,
                             columns=["series_id", "series", "category",
                                      "month", "history_value",
                                      "estimate_value", "kind",
                                      "value_class"])
    est_df = pd.DataFrame(estimates)
    est_df.insert(0, "estimate_id",
                  [f"ML-{i:03d}" for i in range(1, len(est_df) + 1)])
    anom_df = pd.DataFrame(anomalies, columns=[
        "series", "month", "value", "z_score", "note", "value_class"])
    for df in (series_df, est_df, anom_df):
        df.insert(0, "row_id", [f"R{i:03d}" for i in range(1, len(df) + 1)])
    return {"nfp_ml_series": series_df, "nfp_ml_estimates": est_df,
            "nfp_ml_anomalies": anom_df}
