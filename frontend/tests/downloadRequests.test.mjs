import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import ts from "typescript";

// Exercise the actual Dashboard request closure, without a browser/React renderer.
const source = await readFile(new URL("../src/pages/Dashboard.tsx", import.meta.url), "utf8");
const file = ts.createSourceFile("Dashboard.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function initializer(name) {
  let found;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(file) === name) found = node.initializer.getText(file);
    ts.forEachChild(node, visit);
  }
  visit(file);
  assert.ok(found, name);
  return found;
}
function evaluate(expression, scope) {
  const compiled = ts.transpileModule(`const result = ${expression};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
  }).outputText;
  return new Function(...Object.keys(scope), `${compiled}; return result;`)(...Object.values(scope));
}
function harness() {
  const calls = [];
  const results = [];
  const selected = [];
  const timers = new Map();
  let timerId = 0;
  const scope = {
    downloadsActive: { current: true }, latestDownloadQueryKey: { current: "all" }, downloadQueryKey: "all",
    downloadsRequestSeq: { current: 0 }, downloadsAbortController: { current: null },
    downloadPage: 1, downloadPageSize: 20, downloadStatusFilter: "all", downloadRuleFilter: "all",
    downloadPathFilter: "", downloadMinSize: "", downloadMaxSize: "", downloadStartTime: "", downloadEndTime: "",
    api: { get: (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject })) },
    DOWNLOAD_REQUEST_TIMEOUT_MS: evaluate(initializer("DOWNLOAD_REQUEST_TIMEOUT_MS"), {}),
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    axios: { isCancel: e => e?.code === "ERR_CANCELED" }, AbortController,
    console: { error() {} }, formatError: e => e.message,
    setDownloadResult: r => results.push(r), setSelectedIds: ids => selected.push(ids),
  };
  return { scope, calls, results, selected, timers, fetch: evaluate(initializer("fetchDownloads"), scope) };
}
const data = status => ({ data: { items: [{ id: 1, status }], total: 1 } });

test("explicit refresh aborts in-flight request and drops its late success", async () => {
  const h = harness();
  const old = h.fetch();
  const latest = h.fetch();
  assert.equal(h.calls[0].options.signal.aborted, true);
  h.calls[1].resolve(data("downloading")); await latest;
  h.calls[0].resolve(data("completed")); await old;
  assert.equal(h.results.length, 1);
  assert.equal(h.results[0].items[0].status, "downloading");
});

test("stale failure cannot replace the latest success", async () => {
  const h = harness();
  const old = h.fetch(); const latest = h.fetch();
  h.calls[1].resolve(data("downloading")); await latest;
  h.calls[0].reject(new Error("old failure")); await old;
  assert.equal(h.results.length, 1);
  assert.equal(h.results[0].error, null);
});

test("latest failure clears rows and selection, truncates reason, retry recovers", async () => {
  const h = harness();
  const success = h.fetch(); h.calls[0].resolve(data("completed")); await success;
  const failed = h.fetch(); h.calls[1].reject(new Error("x".repeat(300))); await failed;
  assert.deepEqual(h.results.at(-1), { key: "all", items: [], total: 0, error: "x".repeat(240) });
  assert.deepEqual(h.selected.at(-1), []);
  const retry = h.fetch(); h.calls[2].resolve({ data: { items: [], total: 0 } }); await retry;
  assert.equal(h.results.at(-1).error, null);
  assert.equal(h.results.at(-1).total, 0);
});

test("Axios cancellation and AbortError are not failures", async () => {
  for (const error of [Object.assign(new Error("cancel"), { code: "ERR_CANCELED" }), Object.assign(new Error("abort"), { name: "AbortError" })]) {
    const h = harness(); const pending = h.fetch(); h.calls[0].reject(error); await pending;
    assert.equal(h.results.length, 0);
  }
});

test("changed filter hides old data immediately and ignores old action closures", async () => {
  const h = harness(); const pending = h.fetch();
  h.scope.latestDownloadQueryKey.current = "downloading";
  h.calls[0].resolve(data("completed")); await pending;
  await h.fetch();
  assert.equal(h.results.length, 0); assert.equal(h.calls.length, 1);
  assert.equal(evaluate(initializer("currentDownloadResult"), {
    downloadResult: { key: "all", items: [{ status: "completed" }] }, downloadQueryKey: "downloading",
  }), null);
});

test("unmount invalidates in-flight response and prevents new requests", async () => {
  const h = harness(); const pending = h.fetch();
  h.scope.downloadsActive.current = false;
  ++h.scope.downloadsRequestSeq.current;
  h.scope.downloadsAbortController.current.abort();
  h.calls[0].resolve(data("completed")); await pending; await h.fetch();
  assert.equal(h.results.length, 0); assert.equal(h.calls.length, 1);
});

test("feedback distinguishes initial loading, failure, successful empty, and empty page", () => {
  function feedback(extra) {
    const node = evaluate(initializer("downloadListFeedback"), {
      React: { createElement: (type, props, ...children) => ({ type, props, children }) },
      downloadError: null, currentDownloadResult: null, downloads: [], downloadTotal: 0, fetchDownloads() {}, ...extra,
    });
    return { node, text: JSON.stringify(node) };
  }
  assert.match(feedback({}).text, /加载中/);
  const failure = feedback({ downloadError: "database locked" });
  assert.equal(failure.node.props.role, "alert");
  assert.match(failure.text, /加载失败/); assert.match(failure.text, /重试/);
  assert.doesNotMatch(failure.text, /暂无下载记录/);
  assert.match(feedback({ currentDownloadResult: {} }).text, /暂无下载记录/);
  assert.match(feedback({ currentDownloadResult: {}, downloadTotal: 3 }).text, /当前页无记录/);
});

test("poll skips an in-flight request without aborting or changing state", async () => {
  const h = harness();
  const pending = h.fetch();
  const seq = h.scope.downloadsRequestSeq.current;
  const controller = h.scope.downloadsAbortController.current;
  const timers = [...h.timers.entries()];
  await h.fetch({ skipIfInFlight: true });
  assert.equal(h.calls.length, 1);
  assert.equal(controller.signal.aborted, false);
  assert.equal(h.scope.downloadsAbortController.current, controller);
  assert.equal(h.scope.downloadsRequestSeq.current, seq);
  assert.deepEqual([...h.timers.entries()], timers);
  assert.equal(h.results.length, 0);
  assert.equal(h.selected.length, 0);
  h.calls[0].resolve(data("downloading")); await pending;
  assert.equal(h.results.at(-1).items[0].status, "downloading");
  assert.equal(h.timers.size, 0);
});

test("timeout aborts and enters error state, then the next poll recovers", async () => {
  const h = harness();
  const pending = h.fetch();
  const timer = [...h.timers.values()][0];
  assert.equal(timer.delay, 15000);
  // Deliberately leave the transport pending: the timeout must settle the request itself.
  timer.callback(); await pending;
  assert.equal(h.calls[0].options.signal.aborted, true);
  assert.deepEqual(h.results.at(-1), { key: "all", items: [], total: 0, error: "下载列表请求超时（15 秒），请重试" });
  assert.deepEqual(h.selected.at(-1), []);
  assert.equal(h.scope.downloadsAbortController.current, null);
  assert.equal(h.timers.size, 0);
  const retry = h.fetch({ skipIfInFlight: true });
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve(data("downloading")); await retry;
  h.calls[0].resolve(data("completed")); await Promise.resolve();
  assert.equal(h.results.length, 2);
  assert.equal(h.results.at(-1).error, null);
  assert.equal(h.results.at(-1).items[0].status, "downloading");
  assert.equal(h.timers.size, 0);
});
