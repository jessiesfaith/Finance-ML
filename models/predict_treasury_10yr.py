from pathlib import Path
import joblib
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"

saved = joblib.load(MODEL_FILE)

model = saved["model"]
features = saved["features"]

scenario = pd.DataFrame(
    [{
        "treasury_2y": 4.25,
        "fed_funds": 4.25,
        "cpi": 2.80,
        "unemployment": 4.40
    }]
)

prediction = model.predict(scenario[features])[0]

print("INPUT SCENARIO")
print("----------------------")
print(scenario)

print()
print("PREDICTED TREASURY 10Y")
print("----------------------")
print(f"{prediction:.2f}%")