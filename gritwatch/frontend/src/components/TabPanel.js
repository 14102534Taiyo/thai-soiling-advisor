import { buildWeekBars, buildPowerProfile } from "../charts.js";
import { formatThaiDate, formatThaiDateFull } from "../format.js";

export default {
  name: "TabPanel",
  props: {
    dailyGeneration: { type: Array, required: true },
    hourlyToday: { type: Array, required: true },
    events: { type: Array, required: true },
  },
  data() {
    return { activeTab: "week" };
  },
  computed: {
    weekBars() {
      return buildWeekBars(this.dailyGeneration).map((b) => ({ ...b, label: formatThaiDate(b.label) }));
    },
    power() {
      return buildPowerProfile(this.hourlyToday);
    },
  },
  methods: {
    formatThaiDateFull,
    eventKindLabel(kind) {
      return {
        rain_reset: "ฝนล้างแผง",
        tmd_correction: "แก้ไขข้อมูล TMD",
        pm_recalibration: "ปรับเทียบฝุ่น",
        fill_boundary: "ขอบเขตพยากรณ์",
        manual_clean: "ล้างแผงเอง",
      }[kind] ?? kind;
    },
  },
  template: `
  <div class="card blueprint" style="padding:14px 18px; display:flex; flex-direction:column; min-height:0;">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div style="display:flex; gap:6px; margin-bottom:10px;">
      <button class="btn btn-ghost" :class="{active: activeTab==='week'}" @click="activeTab='week'">พลังงาน 7 วัน</button>
      <button class="btn btn-ghost" :class="{active: activeTab==='power'}" @click="activeTab='power'">โปรไฟล์กำลังผลิต</button>
      <button class="btn btn-ghost" :class="{active: activeTab==='events'}" @click="activeTab='events'">เหตุการณ์ระบบ</button>
    </div>

    <div v-if="activeTab==='week'" style="display:flex; flex-direction:column; min-height:0; flex:1;">
      <div style="font-size:12px; color:var(--color-neutral-700); margin-bottom:4px;">พลังงานที่คาดว่าจะผลิตได้ 7 วันข้างหน้า — แผงสะอาด เทียบกับสภาพจริง (kWh)</div>
      <svg viewBox="0 0 560 190" style="width:100%; flex:1; min-height:0;">
        <line x1="34" y1="160" x2="548" y2="160" stroke="var(--color-neutral-400)" stroke-width="1"></line>
        <g v-for="(d,i) in weekBars" :key="i">
          <rect :x="d.x" :y="d.cleanY" :width="d.w" :height="d.cleanH" fill="none" stroke="var(--color-neutral-500)" stroke-width="1"></rect>
          <rect :x="d.x" :y="d.soilY" :width="d.w" :height="d.soilH" fill="var(--color-accent)"></rect>
          <text :x="d.cx" y="176" font-size="11" fill="var(--color-neutral-700)" text-anchor="middle" font-family="var(--font-body)">{{ d.label }}</text>
          <text :x="d.cx" :y="d.valY" font-size="11" fill="var(--color-text)" text-anchor="middle" font-family="var(--font-body)">{{ d.value }}</text>
        </g>
      </svg>
    </div>

    <div v-else-if="activeTab==='power'" style="display:flex; flex-direction:column; min-height:0; flex:1;">
      <div style="font-size:12px; color:var(--color-neutral-700); margin-bottom:4px;">กำลังผลิตวันนี้เทียบความเข้มแสง — สูงสุด {{ power.peakKw.toFixed(1) }} kW</div>
      <svg viewBox="0 0 560 190" style="width:100%; flex:1; min-height:0;">
        <line x1="34" y1="160" x2="548" y2="160" stroke="var(--color-neutral-400)" stroke-width="1"></line>
        <path :d="power.ghiPath" fill="var(--color-neutral-200)" stroke="var(--color-neutral-400)" stroke-width="1"></path>
        <path :d="power.powerPath" fill="none" stroke="var(--color-accent)" stroke-width="2"></path>
        <g fill="var(--color-neutral-600)" font-size="11" font-family="var(--font-body)">
          <text x="34" y="176">06:00</text>
          <text x="270" y="176">12:00</text>
          <text x="516" y="176">18:00</text>
        </g>
      </svg>
    </div>

    <div v-else style="overflow-y:auto; min-height:0; flex:1;">
      <table class="table" style="width:100%; font-size:12px;">
        <thead><tr><th>วันที่</th><th>เหตุการณ์</th><th>ผลต่อโมเดล</th></tr></thead>
        <tbody>
          <tr v-for="(e,i) in events" :key="i">
            <td style="white-space:nowrap;">{{ formatThaiDateFull(e.date) }}</td>
            <td>{{ e.title }}</td>
            <td style="color:var(--color-neutral-700);">{{ e.effect }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  `,
};
