from pathlib import Path
import joblib
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"

saved = joblib.load(MODEL_FILE)

model = saved["model"]
features = saved["features"]

scenarios = pd.DataFrame(
    [
        {
            "scenario": "Bull / Easier Rates",
            "treasury_2y": 3.50,
            "fed_funds": 3.75,
            "cpi": 2.20,
            "unemployment": 4.70
        },
        {
            "scenario": "Base",
            "treasury_2y": 4.25,
            "fed_funds": 4.25,
            "cpi": 2.80,
            "unemployment": 4.40
        },
        {
            "scenario": "Bear / Higher Rates",
            "treasury_2y": 5.00,
            "fed_funds": 5.25,
            "cpi": 4.00,
            "unemployment": 4.00
        }
    ]
)

scenarios["predicted_10y"] = model.predict(
    scenarios[features]
)

print(
    scenarios[
        [
            "scenario",
            "treasury_2y",
            "fed_funds",
            "cpi",
            "unemployment",
            "predicted_10y"
        ]
    ].to_string(index=False)
)