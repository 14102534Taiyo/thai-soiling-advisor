import { buildAcrChart } from "../charts.js?v=6";
import { formatFixed } from "../format.js?v=6";

export default {
  name: "AcrChart",
  props: {
    curve: { type: Array, required: true },
    optimalT: { type: Number, required: true },
    acrAtOptimal: { type: Number, required: true },
  },
  data() {
    return { hover: null };
  },
  computed: {
    chart() {
      return buildAcrChart(this.curve);
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
  <div class="card blueprint" style="padding:14px 18px; display:flex; flex-direction:column; min-height:0;">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div class="card-kicker">ACR</div>
    <div style="font-family:var(--font-heading); font-size:19px; font-weight:600; margin-bottom:2px;">ต้นทุนเทียบจำนวนวันหลังล้างแผง</div>
    <div style="font-size:12px; color:var(--color-neutral-700);">ต่ำสุดที่ T = {{ optimalT }} วัน · {{ formatFixed(acrAtOptimal, 1) }} บาท/วัน</div>
    <svg v-if="curve.length" :viewBox="chart.viewBox" style="width:100%; flex:1; min-height:0; margin-top:6px;"
         @mousemove="onMove" @mouseleave="onLeave">
      <g stroke="var(--color-neutral-300)" stroke-width="1">
        <line x1="40" y1="24" x2="548" y2="24"></line>
        <line x1="40" y1="76" x2="548" y2="76"></line>
        <line x1="40" y1="128" x2="548" y2="128"></line>
        <line x1="40" y1="170" x2="548" y2="170"></line>
      </g>
      <path :d="chart.lossPath" fill="none" stroke="var(--color-neutral-500)" stroke-width="1.5" stroke-dasharray="4 3"></path>
      <path :d="chart.washPath" fill="none" stroke="var(--color-neutral-400)" stroke-width="1.5" stroke-dasharray="2 3"></path>
      <path :d="chart.acrPath" fill="none" stroke="var(--color-accent)" stroke-width="2"></path>
      <circle :cx="chart.minX" :cy="chart.minY" r="4.5" fill="var(--color-accent-700)"></circle>
      <g v-if="hover">
        <line :x1="hover.x" y1="18" :x2="hover.x" y2="170" stroke="var(--color-neutral-600)" stroke-width="1"></line>
        <text :x="hover.tx" y="16" font-size="11" fill="var(--color-text)" :text-anchor="hover.anchor" font-family="var(--font-body)">T={{ hover.t }} · {{ hover.acr }} บาท/วัน</text>
      </g>
      <g fill="var(--color-neutral-600)" font-size="11" font-family="var(--font-body)">
        <text v-for="(l,i) in chart.xLabels" :key="i" :x="l.x" y="188" :text-anchor="i===0?'start':(i===chart.xLabels.length-1?'end':'middle')">{{ l.label }}</text>
      </g>
    </svg>
    <div v-else style="flex:1; display:flex; align-items:center; justify-content:center; color:var(--color-neutral-600); font-size:12px;">
      ยังไม่มีข้อมูลพอสำหรับกราฟ ACR
    </div>
    <div style="display:flex; gap:16px; font-size:11px; color:var(--color-neutral-700); margin-top:2px;">
      <span style="display:flex; align-items:center; gap:6px;"><span style="width:14px; height:2px; background:var(--color-accent);"></span>ACR รวม</span>
      <span style="display:flex; align-items:center; gap:6px;"><span style="width:14px; height:0; border-top:2px dashed var(--color-neutral-500);"></span>ค่าไฟที่เสีย/วัน</span>
      <span style="display:flex; align-items:center; gap:6px;"><span style="width:14px; height:0; border-top:2px dotted var(--color-neutral-400);"></span>ค่าล้างเฉลี่ย/วัน</span>
    </div>
  </div>
  `,
};
