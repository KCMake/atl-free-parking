// Atlanta meter hours as pure functions of Atlanta wall-clock time.
//
// A time is {y, m, d, h, mi} in America/New_York (m is 1-12, h is 0-23).
// scripts/build.py inlines this file into the page, where it defines the
// global `Hours`; tests/hours.test.js loads it with require().
(function (root) {
  "use strict";

  const TZ = "America/New_York";

  // Wall-clock time in Atlanta for an instant (default: now).
  function atlNow(date = new Date()) {
    const parts = {};
    for (const p of new Intl.DateTimeFormat("en-US", {
      timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date)) parts[p.type] = p.value;
    return { y: +parts.year, m: +parts.month, d: +parts.day, h: +parts.hour % 24, mi: +parts.minute };
  }

  // Day of week, 0 = Sunday.
  const dow = w => new Date(Date.UTC(w.y, w.m - 1, w.d)).getUTCDay();

  // Day of the month of the nth weekday `wd` in month m (n = -1: the last one).
  function nthDow(y, m, wd, n) {
    if (n > 0) {
      const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
      return 1 + ((wd - first + 7) % 7) + (n - 1) * 7;
    }
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const lastWd = new Date(Date.UTC(y, m - 1, last)).getUTCDay();
    return last - ((lastWd - wd + 7) % 7);
  }

  // City holidays when meters are not enforced, keyed "month-day".
  function holidays(y) {
    const tg = nthDow(y, 11, 4, 4);
    return {
      "1-1": "New Year’s Day",
      [`1-${nthDow(y, 1, 1, 3)}`]: "Martin Luther King Jr. Day",
      [`5-${nthDow(y, 5, 1, -1)}`]: "Memorial Day",
      "7-4": "Independence Day",
      [`9-${nthDow(y, 9, 1, 1)}`]: "Labor Day",
      "11-11": "Veterans Day",
      [`11-${tg}`]: "Thanksgiving",
      [`11-${tg + 1}`]: "the day after Thanksgiving",
      "12-25": "Christmas Day",
    };
  }
  const holidayMemo = {};
  const holidayName = w => (holidayMemo[w.y] ||= holidays(w.y))[`${w.m}-${w.d}`] || null;

  // City meters run Mon–Sat from 7 AM to `end` (10 PM unless a block listing says otherwise).
  const metersOff = (w, end = 22) => Boolean(holidayName(w)) || dow(w) === 0 || w.h < 7 || w.h >= end;

  // Daytime-restricted blocks open from 7 PM to 7 AM.
  const eveningOpen = w => w.h >= 19 || w.h < 7;

  // One-line summary of the citywide meter rule at time w.
  function meterState(w) {
    const hol = holidayName(w);
    if (hol) return { off: true, text: `City meters are off all day for ${hol}.` };
    if (dow(w) === 0) return { off: true, text: "Sunday: city meters are off all day." };
    if (w.h < 7) return { off: true, text: "Before 7 AM: city meters are off until 7." };
    if (w.h >= 22) return { off: true, text: dow(w) === 6 ? "After 10 PM Saturday: meters are off until Monday 7 AM." : "After 10 PM: city meters are off until 7 AM." };
    return { off: false, text: "City meters are running (Mon–Sat, 7 AM–10 PM)." };
  }

  // What a block shows at time w, given its build-time properties
  // ({status, timed, until}). Metered and evening-only blocks turn "freenow".
  function effectiveStatus(p, w) {
    if (p.status === "paid" && p.timed) return metersOff(w, p.until || 22) ? "freenow" : "paid";
    if (p.status === "night") return eveningOpen(w) ? "freenow" : "check";
    return p.status;
  }

  const api = { TZ, atlNow, dow, nthDow, holidays, holidayName, metersOff, eveningOpen, meterState, effectiveStatus };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Hours = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
