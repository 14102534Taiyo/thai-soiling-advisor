import { formatThaiDate, formatInt, formatFixed } from "../format.js";

export default {
  name: "RecommendationStrip",
  props: {
    recommendation: { type: Object, required: true },
    daysUntilNaturalReset: { type: Number, default: null },
  },
  computed: {
    rec() {
      return this.recommendation;
    },
    headline() {
      if (this.rec.insufficient_data) return "ยังไม่มีข้อมูลพอ";
      if (this.rec.wait_for_rain) {
        const n = this.daysUntilNaturalReset;
        return n != null ? `ไม่ต้องล้าง — ฝนจะล้างให้ในอีก ${n} วัน` : "ไม่ต้องล้าง — ฝนจะล้างให้เอง";
      }
      if (this.rec.optimal_T <= this.rec.min_T) return "ควรล้างวันนี้";
      return `ควรล้างวันที่ ${formatThaiDate(this.rec.recommended_date)}`;
    },
    subline() {
      if (this.rec.insufficient_data) return "ต้องรอข้อมูลอีกอย่างน้อย 2 วันหลังล้างล่าสุดจึงจะคำนวณได้";
      return `ช่วงที่คุ้มพอกัน ${formatThaiDate(this.rec.band_low_date)} – ${formatThaiDate(this.rec.band_high_date)} · ต้นทุนต่ำสุด ${formatFixed(this.rec.acr_at_optimal, 1)} บาท/วัน`;
    },
  },
  template: `
  <section class="card blueprint" style="padding:14px 18px; display:grid; grid-template-columns:1fr auto; align-items:center; gap:24px;">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div style="display:flex; align-items:baseline; gap:18px; flex-wrap:wrap;">
      <span class="card-kicker">คำแนะนำ</span>
      <span style="font-family:var(--font-heading); font-size:30px; font-weight:600; line-height:1;">{{ headline }}</span>
      <span style="font-size:13px; color:var(--color-neutral-700);">{{ subline }}</span>
    </div>
    <div v-if="!rec.insufficient_data" style="display:flex; align-items:center; gap:22px;">
      <div style="text-align:right;">
        <div style="font-size:11px; letter-spacing:0.12em; text-transform:uppercase; color:var(--color-neutral-700);">ประหยัดเทียบล้างทุกวัน</div>
        <div style="font-family:var(--font-heading); font-size:22px; font-weight:600;">{{ formatInt(rec.savings_vs_daily_clean) }} <span style="font-size:12px; font-weight:400; color:var(--color-neutral-700);">บาท/วัน</span></div>
      </div>
      <div style="text-align:right;">
        <div style="font-size:11px; letter-spacing:0.12em; text-transform:uppercase; color:var(--color-neutral-700);">ประหยัดเทียบไม่ล้างเลย</div>
        <div style="font-family:var(--font-heading); font-size:22px; font-weight:600;">{{ formatInt(rec.savings_vs_never_clean) }} <span style="font-size:12px; font-weight:400; color:var(--color-neutral-700);">บาท/วัน</span></div>
      </div>
    </div>
  </section>
  `,
  methods: { formatInt },
};
