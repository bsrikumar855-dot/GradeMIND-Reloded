import assert from "node:assert/strict";
import { test } from "node:test";
import { describe, FINE_STEP, MIN_SIZE, nudge, START_BOX, STEP, type Box } from "./kbbox.ts";

const size = (b: Box): [number, number] => [b[2] - b[0], b[3] - b[1]];
const near = (a: number, b: number) => Math.abs(a - b) < 1e-9;

test("arrows move the box and keep its size", () => {
  const b = nudge(START_BOX, "ArrowRight", false)!;
  assert.ok(near(b[0], 0.12) && near(b[2], 0.52) && near(b[1], 0.1));
  assert.ok(near(size(b)[0], 0.4) && near(size(b)[1], 0.2));
  const d = nudge(b, "ArrowDown", false)!;
  assert.ok(near(d[1], 0.12) && near(d[3], 0.32));
});

test("Shift+arrows resize the bottom-right corner", () => {
  const grow = nudge(START_BOX, "ArrowRight", true)!;
  assert.deepEqual([grow[0], grow[1]], [START_BOX[0], START_BOX[1]]);
  assert.ok(near(grow[2], START_BOX[2] + STEP));
  const taller = nudge(START_BOX, "ArrowDown", true)!;
  assert.ok(near(taller[3], START_BOX[3] + STEP) && near(taller[2], START_BOX[2]));
  const smaller = nudge(START_BOX, "ArrowLeft", true)!;
  assert.ok(near(smaller[2], START_BOX[2] - STEP));
});

test("Alt makes the step fine", () => {
  const b = nudge(START_BOX, "ArrowLeft", false, true)!;
  assert.ok(near(b[0], START_BOX[0] - FINE_STEP));
});

test("the box cannot leave the page", () => {
  let b: Box = START_BOX;
  for (let i = 0; i < 100; i++) b = nudge(b, "ArrowLeft", false)!;
  assert.equal(b[0], 0);
  for (let i = 0; i < 200; i++) b = nudge(b, "ArrowRight", false)!;
  assert.ok(near(b[2], 1) && near(size(b)[0], 0.4));
  for (let i = 0; i < 200; i++) b = nudge(b, "ArrowDown", false)!;
  assert.ok(near(b[3], 1) && near(size(b)[1], 0.2));
  for (let i = 0; i < 200; i++) b = nudge(b, "ArrowUp", false)!;
  assert.equal(b[1], 0);
});

test("resizing stops at the minimum size and at the page edge", () => {
  let b: Box = START_BOX;
  for (let i = 0; i < 100; i++) b = nudge(b, "ArrowLeft", true)!;
  assert.ok(near(size(b)[0], MIN_SIZE));
  for (let i = 0; i < 100; i++) b = nudge(b, "ArrowUp", true)!;
  assert.ok(near(size(b)[1], MIN_SIZE));
  b = START_BOX;
  for (let i = 0; i < 100; i++) b = nudge(b, "ArrowRight", true)!;
  assert.equal(b[2], 1);
});

test("other keys are ignored", () => {
  for (const k of ["Enter", "a", "1", "Tab", "Escape"]) assert.equal(nudge(START_BOX, k, false), null);
});

test("every reachable box is valid for the API (0 <= x0 < x1 <= 1, same for y, min 0.005)", () => {
  let b: Box = START_BOX;
  const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
  let seed = 7;
  for (let i = 0; i < 2000; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    b = nudge(b, keys[seed % 4]!, seed % 3 === 0, seed % 5 === 0)!;
    assert.ok(b[0] >= 0 && b[0] < b[2] && b[2] <= 1 && b[1] >= 0 && b[1] < b[3] && b[3] <= 1);
    assert.ok(b[2] - b[0] >= 0.005 && b[3] - b[1] >= 0.005);
  }
});

test("describe() states position, size and the keys", () => {
  const t = describe(START_BOX);
  assert.match(t, /10% from the left, 10% from the top, 40% wide, 20% high/);
  assert.match(t, /Enter saves/);
});
