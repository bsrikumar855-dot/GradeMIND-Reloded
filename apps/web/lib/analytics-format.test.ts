import assert from "node:assert/strict";
import { test } from "node:test";
import { barWidth, duration, percent } from "./analytics-format.ts";

test("a missing share is never shown as a number", () => {
  assert.equal(percent(null), "n/a");
  assert.equal(percent("0.4000"), "40%");
  assert.equal(percent("0.2500"), "25%");
});

test("durations read naturally and a missing one is n/a", () => {
  assert.equal(duration(null), "n/a");
  assert.equal(duration(45), "45 s");
  assert.equal(duration(600), "10 min");
  assert.equal(duration(7200), "2.0 h");
});

test("bars are relative to the largest count and never divide by zero", () => {
  assert.equal(barWidth(3, 6), 50);
  assert.equal(barWidth(0, 0), 0);
  assert.equal(barWidth(6, 6), 100);
});
