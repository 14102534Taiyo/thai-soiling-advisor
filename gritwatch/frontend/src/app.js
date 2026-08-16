import { createApp, reactive, computed, onMounted } from "vue";
import { api } from "./api.js?v=5";
import { formatThaiDate } from "./format.js?v=5";
import AppHeader from "./components/AppHeader.js?v=5";
import Sidebar from "./components/Sidebar.js?v=5";
import RecommendationStrip from "./components/RecommendationStrip.js?v=5";
import SoilingChart from "./components/SoilingChart.js?v=5";
import AcrChart from "./components/AcrChart.js?v=5";
import TabPanel from "./components/TabPanel.js?v=5";

const DEFAULT_SITE_ID = "bangkok";
const COMPUTE_DEBOUNCE_MS = 600;

const App = {
  components: { AppHeader, Sidebar, RecommendationStrip, SoilingChart, AcrChart, TabPanel },
  setup() {
    // Everything here is local to this browser tab. config/payload are never
    // written anywhere shared -- switching sites or tweaking a slider only
    // ever affects what this one visitor sees (see gritwatch/backend's
    // POST /api/compute: stateless, no database, nothing persisted).
    const state = reactive({
      sites: [],
      siteId: DEFAULT_SITE_ID,
      config: null,
      payload: null,
      loading: true,
      computing: false,
      error: null,
    });

    let computeToken = 0; // guards against an in-flight request landing after a newer one

    const siteLabel = computed(() => {
      if (!state.config) return "";
      return `${state.config.site_name} · ${state.config.lat.toFixed(4)}, ${state.config.lon.toFixed(4)} · ${state.config.kwp} kWp`;
    });
    const dataThrough = computed(() => (state.payload ? formatThaiDate(state.payload.meta.data_through) : ""));

    async function runCompute() {
      const myToken = ++computeToken;
      state.computing = true;
      state.error = null;
      try {
        const payload = await api.compute(state.config);
        if (myToken === computeToken) state.payload = payload;
      } catch (err) {
        if (myToken === computeToken) state.error = err.message ?? String(err);
      } finally {
        if (myToken === computeToken) state.computing = false;
      }
    }

    let debounceTimer = null;
    function scheduleCompute() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(runCompute, COMPUTE_DEBOUNCE_MS);
    }

    async function loadSite(siteId) {
      state.loading = true;
      state.error = null;
      try {
        state.config = await api.getDefaultConfig(siteId);
        state.siteId = siteId;
        await runCompute();
      } catch (err) {
        state.error = err.message ?? String(err);
      } finally {
        state.loading = false;
      }
    }

    function onSiteChanged(newSiteId) {
      if (newSiteId !== state.siteId) loadSite(newSiteId);
    }

    function onFieldChanged(key, value) {
      state.config[key] = value;
      scheduleCompute();
    }

    onMounted(async () => {
      try {
        state.sites = await api.listSites();
      } catch (err) {
        state.error = err.message ?? String(err);
      }
      const initialSite = state.sites.find((s) => s.site_id === DEFAULT_SITE_ID) ? DEFAULT_SITE_ID : state.sites[0]?.site_id;
      if (initialSite) await loadSite(initialSite);
      else {
        state.loading = false;
        state.error = "ไม่พบไซต์ในระบบ";
      }
    });

    return { state, siteLabel, dataThrough, onSiteChanged, onFieldChanged };
  },
  template: `
  <div style="min-height:100vh; min-width:1280px; display:grid; grid-template-rows:auto 1fr;">
    <AppHeader :site-label="siteLabel" :data-through="dataThrough" :computing="state.computing" style="position:sticky; top:0; z-index:1;" />

    <div v-if="state.error" style="grid-column:1/-1; padding:10px 20px; background:var(--color-critical); color:#fff; font-size:13px;">
      {{ state.error }}
    </div>

    <div v-if="state.loading" style="display:flex; align-items:center; justify-content:center; min-height:60vh; color:var(--color-neutral-600);">
      กำลังโหลดข้อมูล...
    </div>

    <div v-else-if="state.config && state.payload" style="display:grid; grid-template-columns:312px 1fr;">
      <Sidebar
        :sites="state.sites" :site-id="state.siteId" :config="state.config"
        @site-changed="onSiteChanged" @field-changed="onFieldChanged" />

      <main style="padding:16px 20px; display:grid; grid-template-rows:auto auto minmax(420px, auto); gap:14px;">
        <RecommendationStrip
          :recommendation="state.payload.recommendation"
          :days-until-natural-reset="state.payload.soiling.days_until_natural_reset" />

        <SoilingChart
          :series="state.payload.soiling.series" :recommendation="state.payload.recommendation"
          :days-since-clean="state.payload.soiling.days_since_clean"
          :soiling-ratio-today="state.payload.soiling.soiling_ratio_today"
          :daily-rate="state.payload.soiling.daily_rate"
          :days-until-natural-reset="state.payload.soiling.days_until_natural_reset" />

        <section style="display:grid; grid-template-columns:1fr 1fr; gap:14px; min-height:420px;">
          <AcrChart :curve="state.payload.acr.curve" :optimal-t="state.payload.recommendation.optimal_T" :acr-at-optimal="state.payload.recommendation.acr_at_optimal" />
          <TabPanel
            :daily-generation="state.payload.generation.daily"
            :hourly-today="state.payload.generation.hourly_today"
            :events="state.payload.events" />
        </section>
      </main>
    </div>
  </div>
  `,
};

createApp(App).mount("#app");
