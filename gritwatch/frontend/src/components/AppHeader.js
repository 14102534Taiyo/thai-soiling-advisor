export default {
  name: "AppHeader",
  props: {
    siteLabel: { type: String, default: "" },
    dataThrough: { type: String, default: "" },
    computing: { type: Boolean, default: false },
  },
  template: `
  <header style="display:flex; align-items:center; justify-content:space-between; padding:0 20px; height:54px; border-bottom:1px solid var(--color-neutral-300);">
    <div style="display:flex; align-items:baseline; gap:14px;">
      <span style="font-family:var(--font-heading); font-weight:700; font-size:22px; letter-spacing:0.08em; text-transform:uppercase;">GritWatch</span>
      <span style="font-size:12px; letter-spacing:0.12em; text-transform:uppercase; color:var(--color-neutral-700);">Soiling Advisor</span>
    </div>
    <div style="display:flex; align-items:center; gap:16px; font-size:13px; color:var(--color-neutral-700);">
      <span v-if="computing" class="tag tag-warn">กำลังคำนวณ...</span>
      <span>{{ siteLabel }}</span>
      <span style="width:1px; height:16px; background:var(--color-neutral-300);"></span>
      <span>ข้อมูลจริงถึง {{ dataThrough }}</span>
    </div>
  </header>
  `,
};
