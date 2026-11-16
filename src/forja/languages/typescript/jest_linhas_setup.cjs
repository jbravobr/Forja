// Injected with --setupFilesAfterEnv for the tests of each line. Before each test it snapshots the
// istanbul statement counters that the instrumented code keeps in global.__coverage__, and after it
// writes, as one NDJSON row in .forja-test-lines/por-teste.ndjson, the start position (compiled
// output line and column) of each statement whose count grew during that test. The counters are
// read, never reset. jest_linhas_reporter.cjs maps the positions to the TypeScript lines.
const fs = require("node:fs");
const path = require("node:path");
const OUT = path.join(process.cwd(), ".forja-test-lines", "por-teste.ndjson");
let before = null;

function counts() {
  const all = {};
  for (const [file, data] of Object.entries(globalThis.__coverage__ || {})) {
    all[file] = { ...data.s };
  }
  return all;
}

beforeEach(() => {
  before = counts();
});

afterEach(() => {
  const state = expect.getState();
  const after = globalThis.__coverage__ || {};
  const statements = {};
  for (const [file, data] of Object.entries(after)) {
    const rel = path.relative(process.cwd(), file).split(path.sep).join("/");
    for (const [key, where] of Object.entries(data.statementMap)) {
      const grew = (data.s[key] || 0) - ((before[file] || {})[key] || 0);
      if (grew > 0) (statements[rel] ||= []).push([where.start.line, where.start.column]);
    }
  }
  const rel = path.relative(process.cwd(), state.testPath).split(path.sep).join("/");
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.appendFileSync(OUT, JSON.stringify({ test: `${rel} > ${state.currentTestName}`, compiled: statements }) + "\n");
});
