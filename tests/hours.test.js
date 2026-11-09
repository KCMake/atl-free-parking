// Tests for web/hours.js. Run: node --test tests/hours.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const H = require("../web/hours.js");

const at = (y, m, d, h, mi = 0) => ({ y, m, d, h, mi });
const TUE = [2026, 11, 10]; // a Tuesday
const SAT = [2026, 11, 14];
const SUN = [2026, 11, 8];

test("floating holidays land on the right day", () => {
  // Expected dates cross-checked against Python's calendar module.
  for (const [y, mlk, memorial, labor, thanks] of [[2026, 19, 25, 7, 26], [2027, 18, 31, 6, 25]]) {
    const h = H.holidays(y);
    assert.equal(h[`1-${mlk}`], "Martin Luther King Jr. Day");
    assert.equal(h[`5-${memorial}`], "Memorial Day");
    assert.equal(h[`9-${labor}`], "Labor Day");
    assert.equal(h[`11-${thanks}`], "Thanksgiving");
    assert.equal(h[`11-${thanks + 1}`], "the day after Thanksgiving");
  }
});

test("meters run Monday to Saturday, 7 AM to 10 PM", () => {
  assert.equal(H.metersOff(at(...TUE, 14)), false);
  assert.equal(H.metersOff(at(...TUE, 6, 59)), true);
  assert.equal(H.metersOff(at(...TUE, 7)), false);
  assert.equal(H.metersOff(at(...TUE, 21, 59)), false);
  assert.equal(H.metersOff(at(...TUE, 22)), true);
  assert.equal(H.metersOff(at(...SAT, 12)), false);
  assert.equal(H.metersOff(at(...SUN, 12)), true);
});

test("meters are off on city holidays", () => {
  assert.equal(H.metersOff(at(2026, 11, 11, 12)), true); // Veterans Day, a Wednesday
  assert.equal(H.metersOff(at(2026, 11, 27, 12)), true); // day after Thanksgiving
  assert.equal(H.metersOff(at(2026, 12, 24, 12)), false); // Christmas Eve is not a holiday
});

test("a block's own meter end hour overrides 10 PM", () => {
  assert.equal(H.metersOff(at(...TUE, 19), 19), true);
  assert.equal(H.metersOff(at(...TUE, 18, 59), 19), false);
  assert.equal(H.metersOff(at(...TUE, 19)), false);
});

test("evening-only blocks open 7 PM to 7 AM", () => {
  assert.equal(H.eveningOpen(at(...TUE, 18, 59)), false);
  assert.equal(H.eveningOpen(at(...TUE, 19)), true);
  assert.equal(H.eveningOpen(at(...TUE, 6)), true);
  assert.equal(H.eveningOpen(at(...TUE, 7)), false);
});

test("effectiveStatus turns blocks free when their rule lifts", () => {
  const day = at(...TUE, 14), evening = at(...TUE, 20), sunday = at(...SUN, 12);
  const meter = { status: "paid", timed: true };
  const earlyMeter = { status: "paid", timed: true, until: 19 };
  const everyDay = { status: "paid", timed: false }; // Atlantic Station
  const night = { status: "night" };

  assert.equal(H.effectiveStatus(meter, day), "paid");
  assert.equal(H.effectiveStatus(meter, evening), "paid");
  assert.equal(H.effectiveStatus(meter, sunday), "freenow");
  assert.equal(H.effectiveStatus(earlyMeter, evening), "freenow");
  assert.equal(H.effectiveStatus(everyDay, sunday), "paid");
  assert.equal(H.effectiveStatus(night, day), "check");
  assert.equal(H.effectiveStatus(night, evening), "freenow");
  assert.equal(H.effectiveStatus({ status: "free" }, day), "free");
});

test("atlNow converts an instant to Atlanta time on both sides of DST", () => {
  assert.deepEqual(H.atlNow(new Date("2026-07-04T16:30:00Z")), at(2026, 7, 4, 12, 30)); // EDT, UTC-4
  assert.deepEqual(H.atlNow(new Date("2026-12-01T04:15:00Z")), at(2026, 11, 30, 23, 15)); // EST, UTC-5
});

test("meterState explains when meters come back", () => {
  assert.equal(H.meterState(at(...TUE, 14)).off, false);
  assert.match(H.meterState(at(...SAT, 23)).text, /until Monday/);
  assert.match(H.meterState(at(2026, 11, 26, 12)).text, /Thanksgiving/);
});
