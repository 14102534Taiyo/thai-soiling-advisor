// Thin fetch wrapper for the gritwatch backend (gritwatch/backend, FastAPI).
//
// Local dev (localhost/127.0.0.1) talks to a locally-run backend. Anywhere
// else (e.g. served from GitHub Pages) talks to the deployed Render service.
// Update PROD_API_BASE once the Render service exists -- its URL is
// "https://<service-name>.onrender.com", where <service-name> is whatever
// you name it when creating the service (render.yaml at the repo root
// defaults it to "gritwatch-api").
const PROD_API_BASE = "https://gritwatch-api.onrender.com";
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
export const API_BASE = isLocal ? "http://127.0.0.1:8000" : PROD_API_BASE;

async function request(path, options) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response wasn't JSON -- keep statusText
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

// A stateless calculator: listSites/getDefaultConfig just hand back starting
// presets, compute() runs the whole pipeline fresh for whatever config is
// passed in. Nothing here is saved or shared between visitors.
export const api = {
  listSites: () => request("/api/sites"),
  getDefaultConfig: (siteId) => request(`/api/sites/${siteId}`),
  compute: (config) => request("/api/compute", { method: "POST", body: JSON.stringify(config) }),
};
