const TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];

// API dates are "YYYY-MM-DD" strings. Parse without a timezone shift
// (new Date("YYYY-MM-DD") parses as UTC midnight, which can display as the
// previous day in a negative-UTC-offset browser) -- split and construct
// a local-time Date instead.
export function parseIsoDate(isoDate) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function formatThaiDate(isoDate) {
  const d = parseIsoDate(isoDate);
  return `${d.getDate()} ${TH_MONTH[d.getMonth()]}`;
}

export function formatThaiDateFull(isoDate) {
  const d = parseIsoDate(isoDate);
  return `${d.getDate()} ${TH_MONTH[d.getMonth()]} ${d.getFullYear() + 543}`;
}

export function formatInt(n) {
  return Math.round(n).toLocaleString("en-US");
}

export function formatFixed(n, digits = 1) {
  return Number(n).toFixed(digits);
}

export function daysBetween(isoA, isoB) {
  const a = parseIsoDate(isoA);
  const b = parseIsoDate(isoB);
  return Math.round((b - a) / 86400000);
}
