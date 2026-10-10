import assert from "node:assert/strict";
import { test } from "node:test";
import { temporaryPassword } from "./temp-password.ts";

test("long enough for the server's 12-character floor, from the safe alphabet only", () => {
  const p = temporaryPassword();
  assert.ok(p.length >= 12);
  assert.match(p, /^[abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789]+$/);
});

test("two passwords differ", () => {
  assert.notEqual(temporaryPassword(), temporaryPassword());
});

test("a biased tail of the random range is rejected, not folded in", () => {
  let first = true;
  const rnd = (n: number) => {
    const a = new Uint32Array(n);
    a.fill(first ? 0xffffffff : 1); // 0xffffffff is above the unbiased limit: it must be skipped
    first = false;
    return a;
  };
  assert.equal(temporaryPassword(rnd, 4), "bbbb");
});
