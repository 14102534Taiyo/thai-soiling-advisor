import { buildSoilingChart } from "../charts.js?v=4";
import { formatFixed } from "../format.js?v=4";

export default {
  name: "SoilingChart",
  props: {
    series: { type: Array, required: true },
    recommendation: { type: Object, required: true },
    daysSinceClean: { type: Number, required: true },
    soilingRatioToday: { type: Number, required: true },
    dailyRate: { type: Number, required: true },
    daysUntilNaturalReset: { type: Number, default: null },
  },
  data() {
    return { hover: null };
  },
  computed: {
    chart() {
      return buildSoilingChart(this.series, this.recommendation, this.daysSinceClean);
    },
    rateText() {
      return `${(this.dailyRate * 100).toFixed(2)}%`;
    },
    nextRainText() {
      return this.daysUntilNaturalReset != null ? `อีก ${this.daysUntilNaturalReset} วัน` : "ไม่พบในช่วงพยากรณ์";
    },
  },
  methods: {
    formatFixed,
    onMove(evt) {
      const rect = evt.currentTarget.getBoundingClientRect();
      const frac = (evt.clientX - rect.left) / rect.width;
      this.hover = this.chart.hoverAt(Math.max(0, Math.min(1, frac)));
    },
    onLeave() {
      this.hover = null;
    },
  },
  template: `
  <section class="card blueprint" style="padding:14px 18px 8px;">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom:10px; flex-wrap:wrap; gap:12px;">
      <div>
        <div class="card-kicker">Soiling ratio</div>
        <div style="font-family:var(--font-heading); font-size:19px; font-weight:600; white-space:nowrap;">ความสะอาดของแผง เทียบฝุ่นและฝน</div>
      </div>
      <div style="display:grid; grid-template-columns:repeat(4, auto); gap:26px; text-align:right; white-space:nowrap;">
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">ความสะอาดวันนี้</div>
          <div style="font-family:var(--font-heading); font-size:26px; font-weight:600; color:var(--color-accent-700);">{{ formatFixed(soilingRatioToday, 3) }}</div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">อัตราสกปรก/วัน</div>
          <div style="font-family:var(--font-heading); font-size:26px; font-weight:600;">{{ rateText }}</div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">วันตั้งแต่ล้างล่าสุด</div>
          <div style="font-family:var(--font-heading); font-size:26px; font-weight:600;">{{ daysSinceClean }}</div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">ฝนล้างครั้งหน้า</div>
          <div style="font-family:var(--font-heading); font-size:26px; font-weight:600;">{{ nextRainText }}</div>
        </div>
      </div>
    </div>
    <div style="position:relative;">
      <svg :viewBox="chart.viewBox" style="width:100%; height:220px; display:block;" @mousemove="onMove" @mouseleave="onLeave">
        <g stroke="var(--color-neutral-300)" stroke-width="1">
          <line v-for="g in chart.gridLabels" :key="g.y" :x1="chart.plot.x0" :y1="g.y" :x2="chart.plot.x1" :y2="g.y"></line>
        </g>
        <g fill="var(--color-neutral-600)" font-size="11" font-family="var(--font-body)">
          <text v-for="g in chart.gridLabels" :key="'l'+g.y" x="8" :y="g.y + 4">{{ g.label }}</text>
        </g>
        <rect v-for="(b,i) in chart.pmBars" :key="i" :x="b.x" :y="b.y" :width="b.w" :height="b.h" fill="var(--color-neutral-300)"></rect>
        <line v-for="(r,i) in chart.rainMarks" :key="i" :x1="r.x" :y1="chart.plot.y0" :x2="r.x" :y2="chart.plot.y1"
              stroke="var(--color-accent-300)" stroke-width="1" stroke-dasharray="3 3"></line>
        <path :d="chart.bandPath" fill="var(--color-accent-200)" opacity="0.55"></path>
        <path :d="chart.soilPath" fill="none" stroke="var(--color-accent)" stroke-width="2"></path>
        <line :x1="chart.todayX" y1="14" :x2="chart.todayX" y2="188" stroke="var(--color-text)" stroke-width="1"></line>
        <text :x="chart.todayX" y="10" font-size="10" fill="var(--color-text)" text-anchor="middle" font-family="var(--font-body)">วันนี้</text>
        <line :x1="chart.recX" y1="14" :x2="chart.recX" y2="188" stroke="var(--color-accent-700)" stroke-width="1.5"></line>
        <circle :cx="chart.recX" :cy="chart.recY" r="4" fill="var(--color-accent-700)"></circle>
        <text :x="chart.recX" y="204" font-size="11" fill="var(--color-accent-700)" text-anchor="middle" font-family="var(--font-body)">วันที่ควรล้าง</text>
        <g v-if="hover">
          <line :x1="hover.x" y1="14" :x2="hover.x" y2="188" stroke="var(--color-neutral-600)" stroke-width="1"></line>
          <circle :cx="hover.x" :cy="hover.y" r="3.5" fill="var(--color-text)"></circle>
        </g>
      </svg>
      <div v-if="hover" style="position:absolute; top:6px; pointer-events:none; background:var(--color-bg); border:1px solid var(--color-neutral-400); padding:6px 9px; font-size:11px; line-height:1.5; white-space:nowrap; transform:translateX(-50%);"
           :style="{ left: hover.cssLeft }">
        <b>{{ hover.date }}</b><br>ความสะอาด {{ hover.sr }}<br>PM2.5 {{ hover.pm }} µg/m³
      </div>
    </div>
  </section>
  `,
};
