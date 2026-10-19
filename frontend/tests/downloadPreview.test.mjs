import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/utils/downloadPreview.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { applyPreviewPage, PREVIEW_PAGE_SIZE } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);

const record = (id, extra = {}) => ({ id, created_at: `2026-10-19 00:00:${String(id).padStart(2, "0")}`, ...extra });

test("refresh keeps loaded history and its next-page cursor", () => {
  const previous = { items: [record(3), record(2), record(1)], page: 2, hasMore: true };
  const next = applyPreviewPage(previous, [record(4), record(3, { file_name: "updated.mp4" })], 1, 100);
  assert.deepEqual(next.items.map(item => item.id), [4, 3, 2, 1]);
  assert.equal(next.items[1].file_name, "updated.mp4");
  assert.equal(next.page, 2);
  assert.equal(next.hasMore, true);
});

test("history pages deduplicate overlap and preserve chronological order", () => {
  const next = applyPreviewPage({ items: [record(3), record(2)], page: 1, hasMore: true }, [record(2), record(1)], 2, 60);
  assert.deepEqual(next.items.map(item => item.id), [3, 2, 1]);
  assert.equal(next.page, 2);
  assert.equal(next.hasMore, false);
});

test("first and subsequent pages use the same offset size", () => {
  assert.equal(PREVIEW_PAGE_SIZE, 30);
  assert.equal(applyPreviewPage({ items: [], page: 1, hasMore: true }, [], 1, 31).hasMore, true);
  assert.equal(applyPreviewPage({ items: [], page: 2, hasMore: true }, [], 1, 31).hasMore, false);
});

test("refresh removes deleted entries in the fetched window while keeping older history", () => {
  const previous = { items: [record(4), record(3), record(2), record(1)], page: 2, hasMore: true };
  const next = applyPreviewPage(previous, [record(4), record(2)], 1, 100);
  assert.deepEqual(next.items.map(item => item.id), [4, 2, 1]);
});

test("a full first page refresh reflects deletions even below the newest record", () => {
  const previous = { items: [record(3), record(2), record(1)], page: 2, hasMore: false };
  const next = applyPreviewPage(previous, [record(3)], 1, 1);
  assert.deepEqual(next.items.map(item => item.id), [3]);
});

test("empty history resets pagination for future downloads", () => {
  const next = applyPreviewPage({ items: [record(1)], page: 3, hasMore: false }, [], 1, 0);
  assert.deepEqual(next, { items: [], page: 1, hasMore: false });
});

test("a record that becomes unavailable is removed from the fetched window", () => {
  const next = applyPreviewPage(
    { items: [record(3), record(2), record(1)], page: 2, hasMore: true },
    [record(3)], 1, 100, [record(3), record(2)],
  );
  assert.deepEqual(next.items.map(item => item.id), [3, 1]);
});

test("loading three pages and polling repeatedly keeps every history record", () => {
  const all = Array.from({ length: 65 }, (_, index) => ({ id: 65 - index, created_at: "2026-10-19 00:00:00" }));
  let history = { items: [], page: 1, hasMore: true };
  for (let page = 1; page <= 3; page++) {
    history = applyPreviewPage(history, all.slice((page - 1) * PREVIEW_PAGE_SIZE, page * PREVIEW_PAGE_SIZE), page, all.length);
  }
  for (let poll = 0; poll < 3; poll++) history = applyPreviewPage(history, all.slice(0, PREVIEW_PAGE_SIZE), 1, all.length);
  assert.deepEqual(history.items.map(item => item.id), all.map(item => item.id));
  assert.equal(history.page, 3);
  assert.equal(history.hasMore, false);
});
