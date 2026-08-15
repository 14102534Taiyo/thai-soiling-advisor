repo: 14102534Taiyo/thai-soiling-advisor
branch: main

## Last sync

date: 2026-08-15T15:40:33Z

### Updated in this project

- Rebuilt the GritWatch dashboard prototype from the real repo modules
- Sidebar parameters mirror app.py's Streamlit sidebar (site, system spec, rain threshold, economics, horizon)
- Soiling and ACR math follow soiling_pv_model.py and economics.py formulas
- Recommendation card shows optimal T, the tolerance band, and both savings figures

## Screen map

| Screen | Repo files |
| --- | --- |
| GritWatch Dashboard v2.dc.html — sidebar parameters | app.py (lines 495-545) |
| GritWatch Dashboard v2.dc.html — recommendation card | src/economics.py (recommend_cleaning), main_demo.py |
| GritWatch Dashboard v2.dc.html — soiling hero chart | src/soiling_pv_model.py (compute_soiling_ratio) |
| GritWatch Dashboard v2.dc.html — ACR chart | src/economics.py (compute_acr_curve) |
| GritWatch Dashboard v2.dc.html — 7-day yield / power profile | src/soiling_pv_model.py (compute_expected_generation*) |
| GritWatch Dashboard v2.dc.html — system events | src/tmd_client.py, src/representative_year.py |
