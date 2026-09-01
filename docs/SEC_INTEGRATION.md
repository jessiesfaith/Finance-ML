# SEC / XBRL Integration (Phase 12)

The public-company test path: SEC EDGAR companyfacts JSON → the same
canonical files, loader, validation, and controls every client goes
through. Nothing about the analytical pipeline is company-specific —
that was the whole design (spec section 20).

## Flow

```
EDGAR companyfacts JSON (free, official)
    → src/financials/sec_adapter.py
        · annual 10-K facts, USD, latest filing per (tag, year)
        · ONES → MILLIONS (canonical scale), recorded in lineage
        · XBRL tags resolved via data/sec/sec_tag_mapping.csv
    → data/sec_staging/<company>/  (gitignored — reviewed, never
      auto-promoted into data/client_fs/)
    → standard loader validation → analyst completes + APPROVES the
      mapping → re-stage → promote
```

Run it (from your machine — SEC blocks many cloud/proxy IPs, and this
returns 403 from hosted environments):

```bash
python src/ingest_sec.py --cik 320193 --from 2022 --to 2024
```

Before heavy use, set a real contact in `USER_AGENT` in
`src/financials/sec_adapter.py` — SEC requests a descriptive
User-Agent with contact details for API traffic.

## The tag mapping is the whole game

Different filers tag the same concept differently (`Revenues` vs
`RevenueFromContractWithCustomerExcludingAssessedTax`); the widened
mapping key `(company_id, source_system, source_account_code)` was built
for exactly this. `data/sec/sec_tag_mapping.csv` is a STARTER template
(~20 common us-gaap tags, `review_status=REVIEW`): per company, the
analyst completes it — the staging report prints every unmapped material
tag sorted by magnitude as the to-do list — and approves it.

**Honesty rule:** with a partial mapping the staged balance sheet will
NOT balance (Control C1) — goodwill, intangibles, accruals, AOCI and the
rest must be mapped first. The staging report says so instead of hiding
it; staged data is promoted only after the mapping is complete and the
controls are clean.

## Tests

`tests/test_sec_adapter.py` runs the adapter end-to-end on a clearly
labeled SYNTHETIC companyfacts fixture (no real SEC data invented or
committed): ONES→MILLIONS conversion, latest-refiling-wins, quarterly
facts excluded, EDGAR lineage, unmapped-tag surfacing, and canonical
loader validation of the staged output.
