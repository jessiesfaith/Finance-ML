"""
Tests for the NFP ML layer (§54/§55 discipline).

The two rules that matter most are proven here:
  1. SEPARATION — building the ML layer leaves every deterministic
     nfp_* export byte-identical.
  2. LABELING — every model output is value_class MODEL ESTIMATE and
     sits beside (never replaces) the management assumption.
"""

import pandas as pd
import pytest

from financials.nfp import build_all
from financials.nfp_ml import (
    ANOMALY_MONTH, ESTIMATE, NFP_DIR, REPORTS, build_ml, load_history,
    regen_history,
)


@pytest.fixture(scope="module")
def ml():
    return build_ml()


def test_committed_history_matches_regeneration():
    fresh = regen_history()
    for name, df in fresh.items():
        committed = pd.read_csv(NFP_DIR / f"{name}.csv")
        pd.testing.assert_frame_equal(committed, df, check_dtype=False)


def test_ml_build_is_deterministic(ml):
    again = build_ml()
    for name, df in ml.items():
        pd.testing.assert_frame_equal(df, again[name])


def _values_match(df, path):
    import io
    roundtrip = pd.read_csv(io.StringIO(df.to_csv(index=False)))
    committed = pd.read_csv(path)
    pd.testing.assert_frame_equal(committed, roundtrip, check_dtype=False)


def test_separation_deterministic_exports_untouched(ml):
    """§54: the deterministic exports are VALUE-identical with the ML
    layer built - no model value ever leaks into them (value-level per
    the audit; the earlier shape-only check could not see a leak)."""
    frames = build_all()
    for name, df in frames.items():
        _values_match(df, REPORTS / f"{name}.csv")
    det_cols = set()
    for name in frames:
        det_cols |= set(pd.read_csv(REPORTS / f"{name}.csv").columns)
    assert "z_score" not in det_cols          # ML vocabulary stays out


def test_every_estimate_is_labeled(ml):
    est = ml["nfp_ml_estimates"]
    assert (est["value_class"] == ESTIMATE).all()
    assert (est["caveat"].str.len() > 10).all()
    anom = ml["nfp_ml_anomalies"]
    assert (anom["value_class"] == ESTIMATE).all()
    ser = ml["nfp_ml_series"]
    assert set(ser["kind"]) == {"HISTORY (SYNTHETIC)", ESTIMATE}
    # audit finding: the series export must carry the taxonomy column too
    assert set(ser["value_class"]) == {"SYNTHETIC", ESTIMATE}
    assert (ser.loc[ser["kind"] == ESTIMATE, "value_class"]
            == ESTIMATE).all()


def test_all_eleven_models_present(ml):
    numbers = sorted(int(m.split(" ", 1)[0])
                     for m in set(ml["nfp_ml_estimates"]["model"]))
    # model 8 (anomaly detection) reports through its own export
    assert numbers == [1, 2, 3, 4, 5, 6, 7, 9, 10, 11]
    assert len(ml["nfp_ml_anomalies"]) >= 1


def test_probabilities_are_probabilities(ml):
    est = ml["nfp_ml_estimates"]
    probs = est[est["unit"] == "probability"]["estimate"].astype(float)
    assert ((probs >= 0) & (probs <= 1)).all()
    scen = est[est["model"].str.startswith("7")]["estimate"].astype(float)
    assert scen.sum() == pytest.approx(1.0, abs=0.01)


def test_model_estimates_sit_beside_management_assumptions(ml):
    est = ml["nfp_ml_estimates"]
    renewal = est[est["model"].str.startswith("3")]
    assert len(renewal) == 5
    assert renewal["management_comparison"].str.contains("ASSUMPTION").all()
    pledge = est[est["model"].str.startswith("10")]
    assert pledge["management_comparison"].str.contains("ASSUMPTION").all()


def test_anomaly_detector_finds_the_seeded_spike(ml):
    anom = ml["nfp_ml_anomalies"]
    assert (anom["month"] == ANOMALY_MONTH).any()
    hit = anom[anom["month"] == ANOMALY_MONTH].iloc[0]
    assert abs(hit["z_score"]) > 3
    # and December's SEASONAL spike is learned, not flagged
    assert not anom["month"].str.endswith("-12").any()


def test_retention_model_surfaces_the_drift(ml):
    est = ml["nfp_ml_estimates"]
    ret = est[est["model"].str.startswith("9")].iloc[0]
    cohorts = load_history()["nfp_donor_cohorts"]
    assert float(ret["estimate"]) < cohorts["retention_rate"].iloc[0]


def test_committed_ml_exports_match_fresh_build(ml):
    for name, df in ml.items():
        _values_match(df, REPORTS / f"{name}.csv")


def test_grant_and_pledge_model_directions(ml):
    """Audit finding: feature wiring was untested - a flipped column or
    renamed category could silently collapse probabilities to the base
    rate. Assert the fitted directions the seeded generator implies,
    and pin one estimate per model."""
    import numpy as np

    from financials.nfp_ml import fit_grant_model, fit_pledge_model
    hist = load_history()
    m3 = fit_grant_model(hist["nfp_grant_renewal_history"])
    p_gov = m3.predict_proba(np.array([[3, 1, 1, 0]]))[0, 1]
    p_corp = m3.predict_proba(np.array([[3, 1, 0, 1]]))[0, 1]
    p_on = m3.predict_proba(np.array([[3, 1, 0, 0]]))[0, 1]
    p_off = m3.predict_proba(np.array([[3, 0, 0, 0]]))[0, 1]
    p_long = m3.predict_proba(np.array([[8, 1, 0, 0]]))[0, 1]
    p_short = m3.predict_proba(np.array([[1, 1, 0, 0]]))[0, 1]
    assert p_gov > p_corp and p_on > p_off and p_long > p_short

    m10 = fit_pledge_model(hist["nfp_pledge_payment_history"])
    p_signed = m10.predict_proba(np.array([[1, 1]]))[0, 1]
    p_verbal = m10.predict_proba(np.array([[0, 1]]))[0, 1]
    assert p_signed > p_verbal

    est = ml["nfp_ml_estimates"]
    renewal = est[est["model"].str.startswith("3")]
    assert float(renewal.iloc[0]["estimate"]) == pytest.approx(0.826,
                                                               abs=0.001)
    pledge = est[est["model"].str.startswith("10")]
    signed_large = pledge[pledge["target"].str.contains("SIGNED")].iloc[0]
    assert float(signed_large["estimate"]) > 0.6
