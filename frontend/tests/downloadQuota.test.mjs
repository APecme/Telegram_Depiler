import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/utils/downloadQuota.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { quotaDraft, quotaPayload } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);

test("editing a saved quota preserves its ID and exact byte capacity", () => {
  const original = { id: 7, period: "custom", custom_value: 6, custom_unit: "hours", limit_bytes: 123456789 };
  assert.deepEqual(quotaPayload([quotaDraft(original)])[0], original);
});

test("all capacities use 1024 units and invalid values cannot be submitted", () => {
  assert.equal(quotaPayload([{ period: "day", value: "1.5", unit: "GB" }])[0].limit_bytes, 1610612736);
  for (const value of ["", "0", "-1", "Infinity", "abc", "1000000000000"])
    assert.throws(() => quotaPayload([{ period: "day", value, unit: "GB" }]));
  assert.throws(() => quotaPayload([{ period: "custom", custom_value: "1.5", custom_unit: "hours", value: "1", unit: "GB" }]));
  assert.throws(() => quotaPayload([]));
});
