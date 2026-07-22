import test from "node:test";
import assert from "node:assert/strict";
import { stackData } from "./uplotStack.js";

test("stacks cumulative sums, preserving x and topmost = total", () => {
  const x = [0, 1, 2];
  const s1 = [1, 2, 3];
  const s2 = [10, 20, 30];
  const s3 = [100, 200, 300];
  const { data, bands } = stackData([x, s1, s2, s3]);
  assert.deepEqual(data[0], x, "x untouched");
  assert.deepEqual(data[1], [1, 2, 3], "bottom series unchanged");
  assert.deepEqual(data[2], [11, 22, 33], "series 2 = s1+s2");
  assert.deepEqual(data[3], [111, 222, 333], "series 3 = s1+s2+s3 (total)");
  // bands: series 2→1 and 3→2
  assert.deepEqual(bands, [{ series: [2, 1] }, { series: [3, 2] }]);
});

test("treats null/undefined/non-numeric as 0 (honest gaps, no NaN)", () => {
  const { data } = stackData([[0, 1], [null, 5], [undefined, "x"]]);
  assert.deepEqual(data[1], [0, 5]);
  assert.deepEqual(data[2], [0, 5]); // 0+0, 5+0
});

test("single series has no bands", () => {
  const { data, bands } = stackData([[0, 1, 2], [4, 5, 6]]);
  assert.deepEqual(data[1], [4, 5, 6]);
  assert.deepEqual(bands, []);
});
