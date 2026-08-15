# Handoff: GritWatch Soiling Dashboard

## Overview

A desktop web dashboard for `14102534Taiyo/thai-soiling-advisor` (branch `main`). It replaces the
Streamlit UI in `app.py` with a fixed-height (940px) application layout: a persistent parameter
sidebar, a recommendation strip, a hero soiling-ratio chart, and a lower row holding the ACR curve
plus a three-tab panel.

The Python physics/economics code in `src/` is unchanged and remains the source of truth. The work
described here is (1) exposing that code as a JSON API, and (2) rebuilding this HTML prototype as
real components that read from it.

## About the design files

`GritWatch Dashboard v2.dc.html` in this bundle is a **design reference created in HTML** — a
prototype showing intended look and behaviour. It is not production code to copy. Recreate it in the
target codebase's environment (React or Vue recommended; no frontend framework exists in the repo
today, so this is a greenfield choice) using that environment's established patterns.

**Important:** every number the prototype displays is computed by a simplified JavaScript model
inside the file (see `renderVals()` / `model()`). Those formulas exist only so the UI reacts to
slider changes during design review. They are NOT the real model. Do not port them. Real values come
from `src/soiling_pv_model.py` and `src/economics.py`.

## Hard constraints — read before proposing an approach

1. **Do not build this in Streamlit.** The repo is a Streamlit app today; that is exactly what is
   being replaced. Streamlit cannot produce this layout — a fixed 940px application shell, a
   312px persistent sidebar of custom controls, four SVG charts with pointer-driven hover, and a
   tabbed panel. Do not propose `st.columns`, `st.tabs`, `streamlit.components.v1`, or a Streamlit
   custom component as a shortcut. Build a real frontend.
2. **Do not modify `src/`.** Those modules are validated and calibrated. Wrap them; do not rewrite,
   "clean up", or reimplement their formulas.
3. **Do not port the prototype's JavaScript math.** The `model()` function in the HTML file is
   throwaway fake data for design review. All real numbers come from `src/` through the API.
4. **Keep the Streamlit app running** during the migration. It stays as a fallback and a
   cross-check: the new dashboard must produce the same numbers `app.py` shows for the same site
   and parameters. Verify this before considering the work done.

## Fidelity

**High-fidelity.** Colors, type, spacing and chart geometry are final. Recreate pixel-accurately
using the design tokens listed below (they come from the Industry design system already applied in
the prototype).

---

## Part 1 — The API ("the pipe")

### Why a background job is required

`get_historical_weather` pulls 90 days of hourly data from Open-Meteo, `compute_expected_generation`
runs a full pvlib PVWatts chain, and `representative_year.py` fetches up to 10 years of history to
build the beyond-horizon fill. End to end this takes tens of seconds to minutes. It cannot run
inside an HTTP request.

**Architecture:**

1. A scheduled job (cron, APScheduler, or Celery beat) runs once each morning per site.
2. It executes the same pipeline as `main_demo.py`: weather + AQ fetch → representative-year fill →
   `compute_soiling_ratio` → `compute_expected_generation` → `compute_acr_curve` →
   `recommend_cleaning`.
3. It writes one JSON blob per site (SQLite, Postgres JSONB, or a file under `.cache/` — a file is
   fine at this scale).
4. FastAPI endpoints read that blob and return it. Response time: milliseconds.

### The parameter problem

The sidebar exposes 13 inputs (kWp, DC/AC, tilt, azimuth, system efficiency, temperature
coefficient, rain threshold, price, wash cost, horizon, lat/lon, site). Changing any of them changes
the model output — but a precomputed blob only holds one parameter set.

Recommended split:

| Parameter | Behaviour |
| --- | --- |
| lat, lon, tilt, azimuth, kWp, DC/AC, system efficiency, temp coefficient, rain threshold, horizon | **Precomputed.** Changing these requires a recompute. Treat as site configuration, saved per site, recomputed by the nightly job. In the UI, editing them marks the panel dirty and offers a "recompute" action (a queued job, with a spinner and a result poll). |
| price_per_kwh, cleaning_cost_per_kwp | **Live.** These only enter `compute_acr_curve` as multipliers on an already-computed soiling/generation series. Ship the raw daily `soiling_ratio` and `expected_generation_kwh` arrays to the client and recompute the ACR curve + optimum in the browser in under a millisecond. Sliders stay instant. |

Alternatively expose `POST /api/acr` that takes the two prices and reruns only `compute_acr_curve` +
`recommend_cleaning` server-side (fast — no network, no pvlib). Do this if you would rather keep all
economics in Python. The client-side path is what the prototype's snappy sliders imply.

### Endpoints

```
GET  /api/sites                          -> SiteSummary[]
GET  /api/sites/{site_id}                -> SiteConfig
PUT  /api/sites/{site_id}                -> SiteConfig        (saves config, queues recompute)
GET  /api/sites/{site_id}/dashboard      -> DashboardPayload  (the one call the page makes on load)
POST /api/sites/{site_id}/recompute      -> { job_id }
GET  /api/jobs/{job_id}                  -> { status, finished_at }
POST /api/acr                            -> AcrResult         (optional; live price recompute)
```

`GET /dashboard` is the important one — it returns everything the screen needs in a single response.
See `data_contract.ts` for the exact shape, and `api_reference.md` for a FastAPI router sketch
wired to the existing `src/` functions.

### Mapping to existing code

| Payload field | Produced by |
| --- | --- |
| `soiling.series[].soiling_ratio` | `soiling_pv_model.compute_soiling_ratio()` |
| `soiling.series[].pm2_5`, `.pm10` | `weather_client.get_historical_air_quality` / `get_forecast_air_quality`, bias-corrected via `air4thai_client` |
| `soiling.series[].rainfall_mm` | `weather_client`, verified by `tmd_client` |
| `soiling.daily_rate` | derived: mean day-over-day drop in soiling_ratio across dry days |
| `generation.daily[]` | `soiling_pv_model.compute_expected_generation()` |
| `generation.hourly_today[]` | `soiling_pv_model.compute_expected_generation_hourly()` |
| `acr.curve[]` | `economics.compute_acr_curve()` (all four columns) |
| `recommendation.*` | `economics.recommend_cleaning()` — return the dict verbatim |
| `events[]` | `tmd_client` correction log (`.cache/tmd_rain_corrections.json`) + representative-year fill boundary + rain resets |
| `meta.data_through` | last real (non-filled) timestamp in the weather/AQ frames |

### Edge cases the UI must handle

These already exist in the Python and must not be flattened away:

- **No manual cleaning needed.** When `optimal_T` hits the edge of the dry stretch and a natural
  rain reset is expected first (`main_demo.py` `hits_edge`), the recommendation strip shows
  "ไม่ต้องล้าง — ฝนจะล้างให้ในอีก N วัน" instead of a date.
- **Assumed last-clean date.** If no rain reset was found on or before today, `last_clean_date` is
  assumed. Flag it in the UI (a `.tag-outline` next to the days-since stat).
- **Multi-modal ACR curve.** The curve can have several local minima 20+ days apart differing by
  under 1% (documented live 2026-08-15). Always present the band (`band_low_date`–`band_high_date`),
  never the single date alone — the prototype's recommendation strip does exactly this.
- **Too few days since reset.** Under 2 days of dry stretch, the optimizer does not run. Show an
  empty state, not a zero.
- **Representative-year fill.** Data past the real forecast horizon is replayed from a source year.
  Render that portion of the soiling chart with reduced opacity or a dashed stroke, and state the
  boundary date.

---

## Part 2 — The screen

### Layout

Root: `height: 940px`, `min-width: 1280px`, no page scroll, `display: grid`,
`grid-template-rows: auto 1fr`.

**Header** — 54px tall, `0 20px` padding, 1px bottom border `--color-neutral-300`.
Left: "GRITWATCH" (Barlow Condensed, 700, 22px, letter-spacing 0.08em, uppercase) + "Soiling
Advisor" (12px, 0.12em, uppercase, `--color-neutral-700`). Right, 13px, gap 16px: site label
("จังหวัด · lat, lon · kWp"), 1px×16px divider, "ข้อมูลจริงถึง {date}", and a `.tag.tag-outline`
reading PROTOTYPE (remove in production).

**Body** — `grid-template-columns: 312px 1fr`.

**Sidebar** (312px, right border, 16px padding, 18px gap, scrolls internally). Five groups, each
with a Barlow Condensed 13px / 0.14em / uppercase / `--color-neutral-700` heading:

1. ทำเลที่ตั้ง — จังหวัด select (full width), ละติจูด + ลองจิจูด (2-col grid, 8px gap)
2. สเปกระบบโซลาร์ — kWp + DC/AC as `.input` number fields (2-col); มุมเอียง (0–45), ทิศทางแผง
   (0–359), ประสิทธิภาพระบบ (70–100%), สัมประสิทธิ์อุณหภูมิ (−0.25 to −0.50 %/°C) as range sliders
   in a 2-col grid, 12px gap. Each slider label is 12px `--color-neutral-700` with the live value
   bold in `--color-text`.
3. เงื่อนไขล้างจากฝน — rain threshold slider 0.2–6.0 mm/hour, step 0.1, default 1.0, plus an 11px
   caption citing Norde Santos et al. 2024.
4. ตัวเลขเศรษฐศาสตร์ — ค่าไฟ (บาท/kWh) + ค่าล้าง (บาท/kWp) number fields; ระยะพยากรณ์ slider
   30–120 days; PM2.5 slider (design-review only — in production PM comes from the API, so remove
   this control).
5. Footer note, 11px, `--color-neutral-600`, pinned with `margin-top: auto`, above a 1px top border.

**Main** — 16px/20px padding, `grid-template-rows: auto auto 1fr`, 14px gap.

*Row 1 — recommendation strip.* `.card.blueprint`, 14px/18px padding, 2-col grid.
Left: kicker "คำแนะนำ" + the headline (Barlow Condensed 600, 30px, e.g. "ควรล้างวันที่ 2 ต.ค." or
"ควรล้างวันนี้") + a 13px `--color-neutral-700` line reading "ช่วงที่คุ้มพอกัน {band} · ต้นทุนต่ำสุด
{acr} บาท/วัน". Right: two right-aligned stats (11px uppercase label over Barlow Condensed 600 22px
value) — ประหยัดเทียบล้างทุกวัน, ประหยัดเทียบไม่ล้างเลย, both in บาท/วัน.

*Row 2 — hero soiling card.* `.card.blueprint`. Header row: kicker "Soiling ratio" + title
"ความสะอาดของแผง เทียบฝุ่นและฝน" (Barlow Condensed 600, 19px, nowrap) on the left; on the right a
4-column auto grid, 26px gap, right-aligned, each 11px uppercase label over a Barlow Condensed 600
26px value — ความสะอาดวันนี้ (accent-700), อัตราสกปรก/วัน, วันตั้งแต่ล้างล่าสุด, ฝนล้างครั้งหน้า.
Chart below: SVG `viewBox="0 0 1180 220"`, 220px tall, full width. Plot area x 52→1160, y 20→182,
y-axis 0.84–1.00 (labels 1.00 / 0.96 / 0.92 / 0.88 at 11px `--color-neutral-600`), four gridlines
plus a baseline in `--color-neutral-300`. Layers back to front: PM2.5 bars (`--color-neutral-300`,
max 34px tall, anchored to y=182), rain-reset markers (1px dashed `--color-accent-300` verticals),
the tolerance band (`--color-accent-200` at 0.55 opacity spanning band_low_T→band_high_T), the
soiling line (2px `--color-accent`), a "วันนี้" marker (1px `--color-text` vertical, 10px label
above), and the recommendation marker (1.5px `--color-accent-700` vertical + r=4 dot + 11px
"วันที่ควรล้าง" label below). Hover anywhere: a `--color-neutral-600` crosshair, an r=3.5
`--color-text` dot on the line, and an absolutely positioned tooltip (1px `--color-neutral-400`
border, `--color-bg` fill, 6px/9px padding, 11px, nowrap, `transform: translateX(-50%)`) showing
date, soiling ratio to 3 decimals, and PM2.5.

*Row 3 — two equal cards, 14px gap.*

**ACR card.** Kicker "ACR", title "ต้นทุนเทียบจำนวนวันหลังล้างแผง", 12px subline "ต่ำสุดที่ T = {n}
วัน · {acr} บาท/วัน". SVG `viewBox="0 0 560 200"`, flexes to fill. Plot x 40→548, y 24→170. Three
series: `loss_cost_per_day` (1.5px dashed `--color-neutral-500`), `clean_cost_per_day` (1.5px
dotted `--color-neutral-400`), `acr` (2px `--color-accent`), plus an r=4.5 `--color-accent-700` dot
at the optimum. Hover: vertical rule + a 11px label at the top reading "T={n} · {acr} บาท/วัน",
anchored `end` past x=430 so it never clips. X labels: 1, midpoint, horizon. A three-item 11px
legend sits under the chart.

**Tab card.** Three `.btn.btn-ghost` tabs (6px/12px padding, 12px, 1px `--color-neutral-400`
border; active = `--color-accent` fill, white text): พลังงาน 7 วัน / โปรไฟล์กำลังผลิต /
เหตุการณ์ระบบ.
- *พลังงาน 7 วัน* — SVG `viewBox="0 0 560 190"`, baseline y=160. Seven day groups at x = 40 + i×72,
  bar width 46: an outlined rect (1px `--color-neutral-500`) for clean-panel kWh and a solid
  `--color-accent` rect for soiled kWh, value label above, Thai short date below.
- *โปรไฟล์กำลังผลิต* — same viewBox. A filled `--color-neutral-200` area for GHI and a 2px
  `--color-accent` line for AC power, 06:00–18:00, with a peak-kW subline.
- *เหตุการณ์ระบบ* — a `.table` (12px) with columns วันที่ / เหตุการณ์ / ผลต่อโมเดล, scrolling
  internally. Rows come from the TMD correction log, rain resets, PM recalibrations, and the
  representative-year fill boundary.

### Interactions

- Every sidebar control updates state on change; charts and the recommendation re-render from it.
  Price and wash-cost changes must feel instant (see the parameter split above).
- Chart hover is pointer-driven off the SVG bounding box: convert clientX to viewBox units, then to
  a day index. No per-point hit targets.
- Tabs are local state, no routing.
- Keyboard focus uses the design system's `:focus-visible` ring — do not override it.
- No page-level scrolling. The sidebar and the events table scroll internally; everything else fits
  940px.

### State

```
site_id, config (13 sidebar params), payload (DashboardPayload | null),
loading, error, recomputeJob (id | null), activeTab ('yield' | 'power' | 'events'),
hoverSoilDay (number | null), hoverAcrT (number | null), configDirty (boolean)
```

Loading state: keep the layout, replace values with skeleton bars — the fixed height means a spinner
that collapses the page will jump. Error state: an inline message in the recommendation strip; leave
the sidebar usable.

---

## Design tokens

All values come from the Industry design system stylesheet. Reference `var(--*)` rather than the
literals where the target codebase can load the stylesheet.

**Color** — ground `--color-bg` #f2f2f3, text `--color-text` #1d1f20, accent `--color-accent`
#5980a6. Ramps `--color-neutral-100..900` and `--color-accent-100..900`. Used here: neutral-200
(chart area fill), neutral-300 (gridlines, borders, PM bars), neutral-400 (dotted series, tooltip
border, tab border), neutral-500 (outlined bars, dashed series), neutral-600 (axis labels, captions),
neutral-700 (secondary text, labels), accent-200 (tolerance band), accent-300 (rain markers),
accent-700 (optimum marker, hero stat, headline accents).

**Type** — `--font-heading` Barlow Condensed, `--font-body` Barlow, with Noto Sans Thai loaded for
Thai text. Sizes in use: 30px/600 (recommendation headline), 26px/600 (hero stats), 22px/700
(wordmark), 22px/600 (savings values), 19px/600 (card titles), 13px/0.14em uppercase (group
headings), 13px (header meta), 12px (labels, tabs, table), 11px (axis labels, captions, tags).

**Spacing** — 20px page horizontal padding, 16px vertical, 14px inter-card gap, 18px/14px card
padding, 18px sidebar group gap, 12px slider-grid gap, 8px field-grid gap, 26px hero-stat gap.

**Frame** — square corners throughout. Cards use `.card.blueprint` with four
`<i class="corner tl|tr|bl|br">` registration marks. No card fills, no shadows, no border radius.

## Files

- `GritWatch Dashboard v2.dc.html` — the prototype (design reference; its JS model is fake data)
- `data_contract.ts` — TypeScript types for every API response; the authoritative JSON shape
- `api_reference.md` — endpoint list and a FastAPI router sketch wired to `src/`
- `github.md` (project root) — repo association and screen map

## Assets

None. Everything is drawn from CSS and inline SVG. Icons, if added, should be Lucide at stroke-width
1.5 per the design system.
