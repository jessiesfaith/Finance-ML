from pathlib import Path

import joblib
import pandas as pd

from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score


# -----------------------------
# FILE LOCATIONS
# -----------------------------

BASE_DIR = Path(__file__).resolve().parents[1]

DB_FILE = BASE_DIR / "data" / "finance_ml.db"
MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"


# -----------------------------
# LOAD HISTORICAL DATA
# -----------------------------

import sqlite3

conn = sqlite3.connect(DB_FILE)

df = pd.read_sql_query(
    """
    SELECT
        date,
        treasury_2y,
        treasury_10y,
        fed_funds,
        cpi,
        unemployment
    FROM macro_history
    ORDER BY date
    """,
    conn
)

conn.close()

print("Historical rows loaded:", len(df))


# -----------------------------
# DEFINE THE ML PROBLEM
# -----------------------------

features = [
    "treasury_2y",
    "fed_funds",
    "cpi",
    "unemployment"
]

target = "treasury_10y"

X = df[features]
y = df[target]


# -----------------------------
# TIME-BASED TRAIN / TEST SPLIT
# -----------------------------

split = int(len(df) * 0.80)

X_train = X.iloc[:split]
X_test = X.iloc[split:]

y_train = y.iloc[:split]
y_test = y.iloc[split:]


# -----------------------------
# TRAIN MODEL
# -----------------------------

model = LinearRegression()

model.fit(X_train, y_train)


# -----------------------------
# TEST MODEL
# -----------------------------

predictions = model.predict(X_test)

mae = mean_absolute_error(y_test, predictions)
r2 = r2_score(y_test, predictions)

print()
print("MODEL RESULTS")
print("----------------------")
print("MAE:", round(mae, 4))
print("R² :", round(r2, 4))


# -----------------------------
# SHOW WHAT MODEL LEARNED
# -----------------------------

print()
print("MODEL COEFFICIENTS")
print("----------------------")

for feature, coefficient in zip(features, model.coef_):
    print(feature, ":", round(coefficient, 4))

print("Intercept:", round(model.intercept_, 4))


# -----------------------------
# SAVE TRAINED MODEL
# -----------------------------

joblib.dump(
    {
        "model": model,
        "features": features
    },
    MODEL_FILE
)

print()
print("Model saved to:")
print(MODEL_FILE)