import { formatFixed, formatInt } from "../format.js?v=6";

export default {
  name: "WeatherTodayCard",
  props: {
    weather: { type: Object, required: true },
  },
  methods: { formatFixed, formatInt },
  template: `
  <section class="card blueprint" style="padding:12px 18px;">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div style="display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:16px;">
      <div>
        <span class="card-kicker">สภาพอากาศวันนี้</span>
      </div>
      <div style="display:grid; grid-template-columns:repeat(6, auto); gap:24px; text-align:right; white-space:nowrap;">
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">ฝนวันนี้</div>
          <div style="font-family:var(--font-heading); font-size:20px; font-weight:600;">{{ formatFixed(weather.rainfall_mm, 1) }} <span style="font-size:11px; font-weight:400; color:var(--color-neutral-700);">มม.</span></div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">PM2.5</div>
          <div style="font-family:var(--font-heading); font-size:20px; font-weight:600;">{{ formatFixed(weather.pm2_5, 1) }} <span style="font-size:11px; font-weight:400; color:var(--color-neutral-700);">µg/m³</span></div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">PM10</div>
          <div style="font-family:var(--font-heading); font-size:20px; font-weight:600;">{{ formatFixed(weather.pm10, 1) }} <span style="font-size:11px; font-weight:400; color:var(--color-neutral-700);">µg/m³</span></div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">อุณหภูมิเฉลี่ย</div>
          <div style="font-family:var(--font-heading); font-size:20px; font-weight:600;">{{ formatFixed(weather.temp_air_c, 1) }} <span style="font-size:11px; font-weight:400; color:var(--color-neutral-700);">°C</span></div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">ลมเฉลี่ย</div>
          <div style="font-family:var(--font-heading); font-size:20px; font-weight:600;">{{ formatFixed(weather.wind_speed_ms, 1) }} <span style="font-size:11px; font-weight:400; color:var(--color-neutral-700);">m/s</span></div>
        </div>
        <div>
          <div style="font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--color-neutral-700);">แสงอาทิตย์เฉลี่ย</div>
          <div style="font-family:var(--font-heading); font-size:20px; font-weight:600;">{{ formatInt(weather.ghi_avg_wm2) }} <span style="font-size:11px; font-weight:400; color:var(--color-neutral-700);">W/m²</span></div>
        </div>
      </div>
    </div>
  </section>
  `,
};
