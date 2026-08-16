// SVG geometry for the four GritWatch charts. Same viewBox/plot-area
// coordinates as the design prototype (GritWatch Dashboard v2.dc.html /
// README.md's Layout section), but driven by real DashboardPayload data
// instead of the prototype's fake model() formulas.

function linePath(points) {
  return points.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
}

function percentile(values, p) {
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
  return sorted[idx];
}

// --- Soiling hero chart: viewBox 0 0 1180 220, plot x 52..1160, y 20..182 ---
const SOIL_X0 = 52, SOIL_X1 = 1160, SOIL_Y0 = 20, SOIL_Y1 = 182;

export function buildSoilingChart(series, recommendation, daysSinceClean) {
  const n = series.length;
  const sx = (i) => SOIL_X0 + ((SOIL_X1 - SOIL_X0) * i) / Math.max(1, n - 1);

  const srValues = series.map((d) => d.soiling_ratio);
  const dataMin = Math.min(...srValues, 0.84);
  const yMin = Math.max(0, Math.floor(dataMin * 25) / 25 - 0.04); // snap below data, 0.04 steps
  const yMax = 1.0;
  const sy = (v) => SOIL_Y0 + ((yMax - v) / (yMax - yMin)) * (SOIL_Y1 - SOIL_Y0);

  const gridLabels = [0, 1, 2, 3].map((i) => {
    const v = yMax - (i * (yMax - yMin)) / 3;
    return { y: sy(v), label: v.toFixed(2) };
  });

  const soilPath = linePath(series.map((d, i) => [sx(i), sy(d.soiling_ratio)]));

  const pmValues = series.map((d) => d.pm2_5);
  const pmScaleMax = Math.max(70, ...pmValues);
  const barWidth = Math.max(1.5, (SOIL_X1 - SOIL_X0) / n - 2);
  const pmBars = series.map((d, i) => {
    const h = Math.min(34, (d.pm2_5 / pmScaleMax) * 34);
    return { x: sx(i) - barWidth / 2, y: SOIL_Y1 - h, w: barWidth, h };
  });

  const rainMarks = series
    .map((d, i) => (d.is_rain_reset ? sx(i) : null))
    .filter((x) => x !== null)
    .map((x) => ({ x }));

  const lo = Math.min(recommendation.band_low_T, n - 1);
  const hi = Math.min(recommendation.band_high_T, n - 1);
  const bandPath = `M${sx(lo).toFixed(1)},${SOIL_Y0} L${sx(hi).toFixed(1)},${SOIL_Y0} L${sx(hi).toFixed(1)},${SOIL_Y1} L${sx(lo).toFixed(1)},${SOIL_Y1} Z`;

  const todayIdx = Math.min(daysSinceClean, n - 1);
  const todayX = sx(todayIdx);

  const recIdx = Math.min(recommendation.optimal_T, n - 1);
  const recX = sx(recIdx);
  const recY = sy(series[recIdx]?.soiling_ratio ?? yMax);

  function hoverAt(clientFractionX) {
    const idx = Math.round(clientFractionX * (n - 1));
    const clamped = Math.max(0, Math.min(n - 1, idx));
    const d = series[clamped];
    return {
      x: sx(clamped),
      y: sy(d.soiling_ratio),
      cssLeft: `${((sx(clamped) - 0) / 1180) * 100}%`,
      date: d.date,
      sr: d.soiling_ratio.toFixed(3),
      pm: Math.round(d.pm2_5),
    };
  }

  return {
    viewBox: "0 0 1180 220",
    plot: { x0: SOIL_X0, x1: SOIL_X1, y0: SOIL_Y0, y1: SOIL_Y1 },
    gridLabels, soilPath, pmBars, rainMarks, bandPath, todayX, recX, recY,
    hoverAt,
  };
}

// --- ACR chart: viewBox 0 0 560 200, plot x 40..548, y 24..170 ---
const ACR_X0 = 40, ACR_X1 = 548, ACR_Y0 = 24, ACR_Y1 = 170;

export function buildAcrChart(curve) {
  const n = curve.length;
  if (n === 0) {
    return { viewBox: "0 0 560 200", acrPath: "", lossPath: "", washPath: "", minX: 0, minY: 0, xLabels: [], hoverAt: () => null };
  }
  const ax = (i) => ACR_X0 + ((ACR_X1 - ACR_X0) * i) / Math.max(1, n - 1);
  // Fit the y-domain to all three series, not just acr -- acr is
  // loss_cost_per_day + clean_cost_per_day, and clean_cost_per_day is a
  // cost-per-wash-amortized-over-T curve that's steep at low T (e.g. a
  // cleaning cost of 1500 THB lands at 1500 THB/day when T=1, but ~10
  // THB/day by T=150) -- an order of magnitude beyond the rest of the data.
  // A domain that fits that spike squashes the interesting part (the U-shape
  // minimum every other point clusters around) to a sliver. Cap the domain
  // at the 90th percentile of all three series pooled together instead: the
  // handful of most-extreme early-T points clip off the top of the plot
  // (still real, just off-screen), and everything else -- including the
  // actual minimum the recommendation is based on -- gets real resolution.
  const allValues = curve.flatMap((p) => [p.acr, p.loss_cost_per_day, p.clean_cost_per_day]);
  const amax = percentile(allValues, 0.9) * 1.08;
  const amin = Math.min(...allValues, 0) * 0.98;
  const ay = (v) => ACR_Y0 + (1 - (v - amin) / Math.max(1e-6, amax - amin)) * (ACR_Y1 - ACR_Y0);

  const acrPath = linePath(curve.map((p, i) => [ax(i), Math.max(18, Math.min(172, ay(p.acr)))]));
  const lossPath = linePath(curve.map((p, i) => [ax(i), Math.max(18, Math.min(172, ay(p.loss_cost_per_day)))]));
  const washPath = linePath(curve.map((p, i) => [ax(i), Math.max(18, Math.min(172, ay(p.clean_cost_per_day)))]));

  let minIdx = 0;
  for (let i = 1; i < n; i++) if (curve[i].acr < curve[minIdx].acr) minIdx = i;

  function hoverAt(fractionX) {
    const idx = Math.max(0, Math.min(n - 1, Math.round(fractionX * (n - 1))));
    const p = curve[idx];
    const x = ax(idx);
    return { x, t: p.T, acr: p.acr.toFixed(1), tx: Math.min(540, Math.max(46, x)), anchor: x > 430 ? "end" : "start" };
  }

  return {
    viewBox: "0 0 560 200",
    plot: { x0: ACR_X0, x1: ACR_X1, y0: ACR_Y0, y1: ACR_Y1 },
    acrPath, lossPath, washPath,
    minX: ax(minIdx), minY: ay(curve[minIdx].acr),
    xLabels: [
      { x: ACR_X0, label: String(curve[0].T) },
      { x: ACR_X0 + (ACR_X1 - ACR_X0) / 2, label: String(curve[Math.floor(n / 2)].T) },
      { x: ACR_X1, label: String(curve[n - 1].T) },
    ],
    hoverAt,
  };
}

// --- Weekly energy bars: viewBox 0 0 560 190, baseline y=160 ---
export function buildWeekBars(dailyGeneration) {
  const week = dailyGeneration.slice(0, 7);
  const maxV = Math.max(...week.map((d) => d.expected_kwh_clean), 1) * 1.05;
  const baseline = 160, maxBarH = 120;
  return week.map((d, i) => {
    const x = 40 + i * 72, w = 46;
    const cleanH = (d.expected_kwh_clean / maxV) * maxBarH;
    const soilH = (d.expected_kwh_soiled / maxV) * maxBarH;
    return {
      x, w, cx: x + w / 2,
      cleanY: baseline - cleanH, cleanH,
      soilY: baseline - soilH, soilH,
      valY: baseline - cleanH - 6,
      label: d.date,
      value: Math.round(d.expected_kwh_soiled),
    };
  });
}

// --- Power profile: viewBox 0 0 560 190, baseline y=160, x 34..534 for 06:00-18:00 ---
export function buildPowerProfile(hourlyToday) {
  if (hourlyToday.length === 0) {
    return { powerPath: "", ghiPath: "", peakKw: 0, hours: [] };
  }
  const baseline = 160;
  const dayHours = hourlyToday.filter((h) => {
    const hour = new Date(h.timestamp).getHours();
    return hour >= 6 && hour <= 18;
  });
  const source = dayHours.length ? dayHours : hourlyToday;
  const peakPower = Math.max(...source.map((h) => h.power_kw), 1);
  const peakGhi = Math.max(...source.map((h) => h.ghi), 1);

  const px = (h) => {
    const t = new Date(h.timestamp);
    const hourFrac = t.getHours() + t.getMinutes() / 60;
    return 34 + Math.max(0, Math.min(1, (hourFrac - 6) / 12)) * 500;
  };
  const powerPath = linePath(source.map((h) => [px(h), baseline - (h.power_kw / peakPower) * 120]));
  const ghiPoints = source.map((h) => [px(h), baseline - (h.ghi / peakGhi) * 132]);
  const ghiPath = `${linePath(ghiPoints)} L534,${baseline} L34,${baseline} Z`;

  return { powerPath, ghiPath, peakKw: peakPower };
}
