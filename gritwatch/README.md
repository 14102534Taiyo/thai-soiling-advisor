# gritwatch/

New GritWatch dashboard build (see `../GritWatch Dashboard handoff/`), kept
self-contained in this folder so it doesn't collide with other work in the
rest of this repo.

**Boundary:** everything in here only *reads* from `../src/` (the calibrated
pvlib/economics modules). It never edits `../app.py`, `../src/`, or anything
else outside `gritwatch/`.

## Structure

- `backend/` — FastAPI JSON API. **Stateless**: no database, no shared
  config, no background jobs. `GET /api/sites` and `GET /api/sites/{id}`
  just hand back starting presets (the 5 provinces' default parameters);
  `POST /api/compute` takes a full config and returns a freshly-computed
  result. Nothing is written to disk, nothing is shared between visitors --
  it's a calculator, not a shared dashboard. Own `pipeline.py` copy of the
  weather->soiling->economics wiring (mirrors `app.py`'s `run_pipeline`),
  own `requirements.txt`.
- `frontend/` — Vue 3 dashboard UI. Every sidebar edit (debounced ~600ms)
  calls `POST /api/compute` and updates the view live -- no save/recompute
  button, no state that persists across a reload or affects another
  visitor. No build step (Node/npm isn't installed on the machine this was
  built on) -- Vue is vendored locally at
  `frontend/vendor/vue.esm-browser.prod.js` and loaded via a native
  `<script type="importmap">`, components are plain `.js` files with
  template strings. Any static file server works; no bundler needed.

## Running locally

```bash
# from the project root, one terminal each
pip install -r gritwatch/backend/requirements.txt
uvicorn gritwatch.backend.main:app --reload --port 8000

python -m http.server 5173 --directory gritwatch/frontend
```

Open `http://127.0.0.1:5173`. Swagger UI for the API is at
`http://127.0.0.1:8000/docs`.

## Deploying (GitHub Pages + Render)

Two pieces, deployed separately:

- **Frontend → GitHub Pages.** `.github/workflows/gritwatch-pages.yml`
  publishes `gritwatch/frontend/` on every push to `main` that touches that
  folder. One-time setup: repo Settings -> Pages -> Source -> "GitHub
  Actions".
- **Backend → Render.** `render.yaml` at the repo root defines the web
  service (`gritwatch-api`, free plan). On Render: New -> Blueprint -> pick
  this repo. It reads `render.yaml` and creates the service automatically.

After the Render service exists, if you named it something other than
`gritwatch-api`, update `PROD_API_BASE` in
[`frontend/src/api.js`](frontend/src/api.js) to match its real
`https://<name>.onrender.com` URL, then push.

**Known limitations of this setup, before pointing people at it:**

- No authentication, and no rate limiting. Anyone with the URL can call
  `POST /api/compute` repeatedly with any lat/lon, which fans out to
  Open-Meteo/Air4Thai/TMD -- fine for casual sharing, but there's nothing
  stopping someone from hammering it. Add rate limiting in
  [`backend/routes.py`](backend/routes.py) before sharing widely.
- Render's free web service spins down after 15 minutes idle; the first
  request after that takes about a minute to respond while it wakes back
  up. Since the backend keeps no state, this only costs that one slow
  request -- nothing is lost or reset.
- Each `POST /api/compute` call takes roughly 1-2 seconds once a location's
  weather has been fetched before (pvlib recompute only), but can take much
  longer -- "tens of seconds to minutes" per the original design notes --
  the first time a genuinely new lat/lon is requested (representative-year
  weather fetch). The sidebar's 5 presets are always warm after their first
  use; a visitor typing in an arbitrary custom lat/lon may wait a while.
