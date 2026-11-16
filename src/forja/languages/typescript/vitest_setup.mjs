// Injected by vitest_forja.config.mjs (setupFiles) when the tests of each line are wanted. Per test,
// the V8 precise coverage of the worker is reset before the test and taken after it: the raw ranges
// of the project's own scripts, with each script's transformed source, are written to
// .forja-test-lines/v8-por-teste/ (one file per test), and vitest_linhas.mjs converts them.
import { Session } from "node:inspector";
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { pathToFileURL } from "node:url";

const root = process.cwd();
const vitest = await import(pathToFileURL(join(root, "node_modules/vitest/dist/index.js")).href);
const out = join(root, ".forja-test-lines", "v8-por-teste");
const projectUrl = pathToFileURL(root).href;
const session = new Session();
session.connect();
const post = (method, params) =>
  new Promise((ok, fail) => session.post(method, params, (e, r) => (e ? fail(e) : ok(r))));
await post("Profiler.enable");
await post("Debugger.enable");
await post("Profiler.startPreciseCoverage", { callCount: true, detailed: true });

vitest.beforeEach(async () => {
  await post("Profiler.takePreciseCoverage"); // resets the counters: this test starts from zero
});

vitest.afterEach(async () => {
  const state = vitest.expect.getState();
  const { result } = await post("Profiler.takePreciseCoverage");
  const scripts = result.filter(
    (s) => s.url && s.url.startsWith(projectUrl) && !s.url.includes("/node_modules/"),
  );
  if (scripts.length === 0) return;
  mkdirSync(out, { recursive: true });
  const rows = [];
  for (const script of scripts) {
    const { scriptSource } = await post("Debugger.getScriptSource", { scriptId: script.scriptId });
    rows.push({ url: script.url, functions: script.functions, source: scriptSource });
  }
  const rel = relative(root, state.testPath).split(sep).join("/");
  const name = `${rel} > ${state.currentTestName}`;
  const file = `${Date.now()}-${Math.random().toString(36).slice(2)}.json`;
  writeFileSync(join(out, file), JSON.stringify({ test: name, rows }));
});
