// Sidebar: 4 parameter groups + footer, per the handoff's Layout section.
// Every field is "live" -- changing anything (debounced in app.js) reruns
// the calculator and updates the view. Nothing here is saved or shared; a
// site switch just re-seeds these fields from that preset's defaults.
export default {
  name: "Sidebar",
  props: {
    sites: { type: Array, required: true },
    siteId: { type: String, required: true },
    config: { type: Object, required: true },
  },
  emits: ["site-changed", "field-changed"],
  computed: {
    gammaSlider: {
      get() {
        return Math.round(-this.config.gamma_pdc * 10000);
      },
      set(v) {
        this.onField("gamma_pdc", -v / 10000);
      },
    },
    sysEffPct: {
      get() {
        return Math.round(this.config.system_efficiency * 100);
      },
      set(v) {
        this.onField("system_efficiency", v / 100);
      },
    },
  },
  methods: {
    onField(key, value) {
      this.$emit("field-changed", key, value);
    },
    onNumberInput(key, event) {
      const v = parseFloat(event.target.value);
      if (!Number.isNaN(v)) this.onField(key, v);
    },
  },
  template: `
  <aside style="border-right:1px solid var(--color-neutral-300); padding:16px; overflow-y:auto; display:flex; flex-direction:column; gap:18px;">

    <div>
      <div class="card-kicker" style="margin-bottom:8px;">ทำเลที่ตั้ง</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <label style="grid-column:1 / -1; font-size:12px; color:var(--color-neutral-700);">เลือกจังหวัด
          <select class="input" style="width:100%; margin-top:4px;"
                  :value="siteId" @change="$emit('site-changed', $event.target.value)">
            <option v-for="s in sites" :key="s.site_id" :value="s.site_id">{{ s.site_name }}</option>
          </select>
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">ละติจูด
          <input class="input" type="number" step="0.0001" :value="config.lat" style="width:100%; margin-top:4px;"
                 @change="onNumberInput('lat', $event)">
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">ลองจิจูด
          <input class="input" type="number" step="0.0001" :value="config.lon" style="width:100%; margin-top:4px;"
                 @change="onNumberInput('lon', $event)">
        </label>
      </div>
    </div>

    <div>
      <div class="card-kicker" style="margin-bottom:8px;">สเปกระบบโซลาร์</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <label style="font-size:12px; color:var(--color-neutral-700);">ขนาดระบบ (kWp)
          <input class="input" type="number" step="1" min="1" :value="config.kwp" style="width:100%; margin-top:4px;"
                 @change="onNumberInput('kwp', $event)">
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">อัตราส่วน DC/AC
          <input class="input" type="number" step="0.05" min="1" :value="config.dc_ac_ratio" style="width:100%; margin-top:4px;"
                 @change="onNumberInput('dc_ac_ratio', $event)">
        </label>
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px;">
        <label style="font-size:12px; color:var(--color-neutral-700);">มุมเอียง <b style="color:var(--color-text);">{{ config.tilt }}°</b>
          <input type="range" min="0" max="45" step="1" :value="config.tilt" style="width:100%;"
                 @input="onNumberInput('tilt', $event)">
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">ทิศทางแผง <b style="color:var(--color-text);">{{ config.azimuth }}°</b>
          <input type="range" min="0" max="359" step="1" :value="config.azimuth" style="width:100%;"
                 @input="onNumberInput('azimuth', $event)">
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">ประสิทธิภาพระบบ <b style="color:var(--color-text);">{{ sysEffPct }}%</b>
          <input type="range" min="70" max="100" step="1" :value="sysEffPct" style="width:100%;"
                 @input="sysEffPct = parseFloat($event.target.value)">
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">สัมประสิทธิ์อุณหภูมิ <b style="color:var(--color-text);">-0.{{ String(gammaSlider).padStart(2,'0') }}%/°C</b>
          <input type="range" min="25" max="50" step="1" :value="gammaSlider" style="width:100%;"
                 @input="gammaSlider = parseFloat($event.target.value)">
        </label>
      </div>
    </div>

    <div>
      <div class="card-kicker" style="margin-bottom:8px;">เงื่อนไขล้างจากฝน</div>
      <label style="font-size:12px; color:var(--color-neutral-700);">เกณฑ์ฝน <b style="color:var(--color-text);">{{ config.cleaning_threshold_mm.toFixed(1) }} มม./ชม.</b>
        <input type="range" min="0.2" max="6" step="0.1" :value="config.cleaning_threshold_mm" style="width:100%;"
               @input="onNumberInput('cleaning_threshold_mm', $event)">
      </label>
      <div style="font-size:11px; color:var(--color-neutral-600); margin-top:4px;">เกณฑ์รายชั่วโมง ตาม Norde Santos et al. 2024 — ค่าตั้งต้น 1.0 มม./ชม.</div>
    </div>

    <div>
      <div class="card-kicker" style="margin-bottom:8px;">ตัวเลขเศรษฐศาสตร์</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <label style="font-size:12px; color:var(--color-neutral-700);">ค่าไฟ (บาท/kWh)
          <input class="input" type="number" step="0.1" min="0.1" :value="config.price_per_kwh" style="width:100%; margin-top:4px;"
                 @change="onNumberInput('price_per_kwh', $event)">
        </label>
        <label style="font-size:12px; color:var(--color-neutral-700);">ค่าล้าง (บาท/kWp)
          <input class="input" type="number" step="0.5" min="0.1" :value="config.cleaning_cost_per_kwp" style="width:100%; margin-top:4px;"
                 @change="onNumberInput('cleaning_cost_per_kwp', $event)">
        </label>
      </div>
      <label style="display:block; font-size:12px; color:var(--color-neutral-700); margin-top:12px;">ระยะพยากรณ์ <b style="color:var(--color-text);">{{ config.horizon_days }} วัน</b>
        <input type="range" min="30" max="120" step="5" :value="config.horizon_days" style="width:100%;"
               @input="onNumberInput('horizon_days', $event)">
      </label>
    </div>

    <div style="margin-top:auto; font-size:11px; color:var(--color-neutral-600); border-top:1px solid var(--color-neutral-300); padding-top:10px;">
      ตัวเลขทั้งหมดคำนวณจริงผ่าน pvlib (HSU + PVWatts) และ economics.py ผ่าน gritwatch API — ปรับที่นี่ไม่กระทบคนอื่น ไม่มีการบันทึกค่า
    </div>
  </aside>
  `,
};
