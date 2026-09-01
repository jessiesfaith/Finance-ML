"""
SEC / XBRL ingestion adapter — Phase 12 (spec section 20).

Translates SEC EDGAR "companyfacts" JSON (free, official:
https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json) into the
CANONICAL client-financials format — the same files, schemas, loader,
and validation every client goes through. The canonical layer stays
strict; this adapter absorbs the messiness (docs/SCHEMAS.md pattern):

  * selects annual 10-K facts (fp=FY), USD units, latest filing per
    (tag, fiscal year); instant facts for the balance sheet, ~1-year
    duration facts for flows;
  * converts values from ONES (as XBRL reports them) to the canonical
    MILLIONS scale, recording the conversion in source_note;
  * maps XBRL tags through data/sec/sec_tag_mapping.csv — a STARTER
    template (review_status=REVIEW: an analyst approves per company).
    Different filers use different tags for the same concept; that is
    exactly what the (source_system, source_account_code) mapping key
    was widened for;
  * SURFACES every unmapped material tag instead of hiding it — a real
    filer will not balance (C1) until its mapping is completed, and the
    staging report says so honestly;
  * writes to a STAGING directory (data/sec_staging/<CIK>/, gitignored),
    never into data/client_fs/ — an analyst reviews mapping + validation
    before any staged data is promoted.

No SEC data is invented anywhere: tests run on a clearly-labeled
synthetic companyfacts fixture; real data comes only from EDGAR.
"""

import json
import logging
import urllib.request
from pathlib import Path

import pandas as pd

from financials.loader import _read_csv
from financials.schemas import (
    ACCOUNT_MAPPING,
    ALL_SCHEMAS,
    CLIENT_FS_RAW,
)

log = logging.getLogger("financials.sec")

BASE_DIR = Path(__file__).resolve().parents[2]
TAG_MAPPING_FILE = BASE_DIR / "data" / "sec" / "sec_tag_mapping.csv"
STAGING_ROOT = BASE_DIR / "data" / "sec_staging"

# SEC requests a descriptive User-Agent with a contact. EDIT THIS to your
# own contact before heavy use (docs/SEC_INTEGRATION.md).
USER_AGENT = "Finance-ML-learning-project (contact: set-me-in-sec_adapter.py)"

MILLION = 1_000_000.0


def fetch_companyfacts(cik: str) -> dict:
    """Fetch companyfacts JSON from EDGAR (free, official)."""
    cik10 = str(int(cik)).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    log.info("fetching %s", url)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def load_tag_mapping() -> pd.DataFrame:
    return _read_csv(TAG_MAPPING_FILE)


def _is_annual_duration(entry) -> bool:
    if "start" not in entry or not entry.get("start"):
        return False
    span = (pd.Timestamp(entry["end"]) - pd.Timestamp(entry["start"])).days
    return 300 <= span <= 400


def _select_facts(tag_data, statement_type, fy_min, fy_max):
    """Latest 10-K/FY USD fact per fiscal year for one tag."""
    per_year = {}
    for entry in tag_data.get("units", {}).get("USD", []):
        if entry.get("form") != "10-K" or entry.get("fp") != "FY":
            continue
        fy = entry.get("fy")
        if fy is None or not (fy_min <= fy <= fy_max):
            continue
        if statement_type == "BS":
            if "start" in entry and entry.get("start"):
                continue                      # BS facts are instants
        elif not _is_annual_duration(entry):
            continue                          # flows must span ~1 year
        current = per_year.get(fy)
        if current is None or entry.get("accn", "") > current.get("accn", ""):
            per_year[fy] = entry
    return per_year


def parse_companyfacts(facts_json: dict, fy_min: int, fy_max: int) -> dict:
    """
    Build canonical frames from one companyfacts document. Returns
    {"tables": {filename: DataFrame}, "unmapped": DataFrame} — unmapped
    material tags are the analyst's mapping to-do list, never hidden.
    """
    cik = str(int(facts_json["cik"])).zfill(10)
    entity_name = facts_json.get("entityName", f"CIK {cik}")
    company_id = f"SEC{cik}"
    entity_id = "ENT_SEC"
    gaap = facts_json.get("facts", {}).get("us-gaap", {})

    mapping = load_tag_mapping()
    mapped_tags = dict(zip(mapping["source_account_code"],
                           mapping["statement_type"]))

    raw_rows, year_ends = [], {}
    for tag, statement_type in mapped_tags.items():
        if tag not in gaap:
            continue
        for fy, entry in _select_facts(gaap[tag], statement_type,
                                       fy_min, fy_max).items():
            amount = round(entry["val"] / MILLION, 4)
            year_ends[fy] = max(year_ends.get(fy, ""), entry["end"])
            raw_rows.append({
                "company_id": company_id, "entity_id": entity_id,
                "period_id": f"FY{fy}", "statement_type": statement_type,
                "source_system": "SEC_XBRL", "source_account_code": tag,
                "source_account_name": gaap[tag].get("label") or tag,
                "amount_local": amount, "local_currency": "USD",
                "fx_rate_to_reporting": 1.0, "amount_reporting": amount,
                "reporting_currency": "USD", "scenario": "ACTUAL",
                "source_file": f"SEC EDGAR companyfacts CIK{cik}",
                "source_sheet": f"us-gaap 10-K {entry.get('accn', '')}",
                "source_row": int(fy),
                "source_note": "XBRL value converted ONES->MILLIONS; "
                               f"filed {entry.get('filed', '')}",
                "load_id": f"SEC-{cik}",
                "load_timestamp": f"{entry.get('filed', entry['end'])}T00:00:00Z",
            })

    # Unmapped material tags — the analyst's to-do, sorted by magnitude.
    unmapped_rows = []
    for tag, tag_data in gaap.items():
        if tag in mapped_tags:
            continue
        annual = {
            fy: e for fy, e in _select_facts(
                tag_data, "ANY", fy_min, fy_max).items()
        }
        instants = {
            fy: e for fy, e in _select_facts(
                tag_data, "BS", fy_min, fy_max).items()
        }
        chosen = annual or instants
        if not chosen:
            continue
        latest_fy = max(chosen)
        unmapped_rows.append({
            "source_account_code": tag,
            "label": tag_data.get("label") or tag,
            "latest_fy": latest_fy,
            "latest_amount_musd": round(chosen[latest_fy]["val"] / MILLION, 1),
        })
    unmapped = pd.DataFrame(
        unmapped_rows,
        columns=["source_account_code", "label", "latest_fy",
                 "latest_amount_musd"],
    ).sort_values("latest_amount_musd", key=lambda s: s.abs(),
                  ascending=False).reset_index(drop=True)

    # Canonical companion tables.
    fiscal_year_end = (
        pd.Timestamp(max(year_ends.values())).strftime("%m-%d")
        if year_ends else "12-31"
    )
    company_master = pd.DataFrame([{
        "company_id": company_id,
        "company_name": f"{entity_name} (SEC XBRL, CIK {cik})",
        "reporting_currency": "USD", "amount_scale": "MILLIONS",
        "fiscal_year_end": fiscal_year_end,
        "accounting_standard": "US_GAAP", "source_system": "SEC_XBRL",
        "industry": "UNCLASSIFIED", "subindustry": "",
        "country": "US", "active_flag": "Y",
    }])
    entity_master = pd.DataFrame([{
        "company_id": company_id, "entity_id": entity_id,
        "parent_entity_id": "",
        "entity_name": f"{entity_name} (consolidated filer)",
        "entity_type": "PARENT", "ownership_pct": 100,
        "functional_currency": "USD", "country": "US",
        "consolidation_method": "FULL", "elimination_entity_flag": "N",
        "active_flag": "Y",
    }])
    period_rows = []
    for fy, end in sorted(year_ends.items()):
        end_ts = pd.Timestamp(end)
        start_ts = end_ts - pd.DateOffset(years=1) + pd.Timedelta(days=1)
        period_rows.append({
            "period_id": f"FY{fy}", "fiscal_year": fy, "fiscal_quarter": "",
            "period_start": start_ts.strftime("%Y-%m-%d"),
            "period_end": end_ts.strftime("%Y-%m-%d"),
            "period_type": "ANNUAL", "is_historical": "Y",
            "is_forecast": "N",
            "days_in_period": (end_ts - start_ts).days + 1,
        })

    tables = {
        "company_master.csv": company_master,
        "entity_master.csv": entity_master,
        "period_master.csv": pd.DataFrame(period_rows),
        "account_mapping.csv": mapping,
        "fx_rates.csv": pd.DataFrame(columns=[
            c.name for s in ALL_SCHEMAS if s.table == "fx_rates"
            for c in s.columns
        ]),
        "client_fs_raw.csv": pd.DataFrame(
            raw_rows, columns=CLIENT_FS_RAW.column_names()
        ).sort_values(
            ["period_id", "statement_type", "source_account_code"]
        ).reset_index(drop=True),
    }
    log.info(
        "parsed CIK %s: %d raw rows over %d fiscal year(s); %d unmapped tag(s)",
        cik, len(raw_rows), len(year_ends), len(unmapped),
    )
    return {"tables": tables, "unmapped": unmapped, "company_id": company_id}


def write_staging(parsed: dict, staging_root=None) -> Path:
    """Write canonical CSVs to data/sec_staging/<company>/ (gitignored)."""
    root = Path(staging_root) if staging_root else STAGING_ROOT
    target = root / parsed["company_id"]
    target.mkdir(parents=True, exist_ok=True)
    for name, frame in parsed["tables"].items():
        frame.to_csv(target / name, index=False)
    parsed["unmapped"].to_csv(target / "unmapped_tags.csv", index=False)
    log.info("staged canonical files in %s", target)
    return target
