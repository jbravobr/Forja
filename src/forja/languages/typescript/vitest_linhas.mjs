// Usage (in the project's folder): node vitest_linhas.mjs
// Runs the project's tests once, with the Forja's configuration that records the V8 coverage of each
// test (vitest_setup.mjs). Then converts each test's ranges to the statements it ran, with the same
// converter Vitest's v8 provider uses, and writes the lines of each test to
// .forja-test-lines/linhas.json. Exits with the status of the run: nonzero means no lines.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = process.cwd();
const here = fileURLToPath(new URL(".", import.meta.url));
const directory = join(root, ".forja-test-lines");
const raw = join(directory, "v8-por-teste");

const run = spawnSync(
  process.execPath,
  [
    join(root, "node_modules/vitest/vitest.mjs"),
    "run",
    "--reporter=tap-flat",
    `--config=${join(here, "vitest_forja.config.mjs")}`,
  ],
  {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, FORJA_TEST_LINES: "1", NO_COLOR: "1" },
  },
);
if (run.status !== 0) process.exit(run.status ?? 1);

const providerRequire = createRequire(join(root, "node_modules/@vitest/coverage-v8/package.json"));
const astV8ToIstanbul = (
  await import(pathToFileURL(providerRequire.resolve("ast-v8-to-istanbul")).href)
).default;
const { parseAstAsync } = await import(
  pathToFileURL(join(root, "node_modules/vitest/dist/node.js")).href
);

const cache = new Map();
const sourceLines = (path) => {
  if (!cache.has(path)) cache.set(path, readFileSync(path, "utf8").split("\n"));
  return cache.get(path);
};

const result = {};
const files = existsSync(raw) ? readdirSync(raw) : [];
for (const file of files) {
  const { test, rows } = JSON.parse(readFileSync(join(raw, file), "utf8"));
  for (const row of rows) {
    const m = /sourceMappingURL=data:application\/json;(?:charset=utf-8;)?base64,([A-Za-z0-9+/=]+)/.exec(
      row.source,
    );
    const sourceMap = m ? JSON.parse(Buffer.from(m[1], "base64").toString("utf8")) : undefined;
    const ast = await parseAstAsync(row.source);
    const coverage = await astV8ToIstanbul({
      code: row.source,
      sourceMap,
      ast,
      coverage: { functions: row.functions, url: row.url },
      ignoreClassMethods: [],
      wrapperLength: 0,
      ignoreNode: () => false,
    });
    for (const data of Object.values(coverage)) {
      const rel = relative(root, data.path).split(sep).join("/");
      for (const [key, where] of Object.entries(data.statementMap)) {
        const text = sourceLines(data.path)[where.start.line - 1] || "";
        // An export line runs when a test reads the import binding (Vite's export getter): not the
        // test's own code.
        if (data.s[key] > 0 && !/^\s*export\s/.test(text)) {
          const ids = ((result[rel] ||= {})[where.start.line] ||= new Set());
          ids.add(test);
        }
      }
    }
  }
}

const sorted = {};
for (const rel of Object.keys(result).sort()) {
  sorted[rel] = {};
  for (const line of Object.keys(result[rel]).map(Number).sort((a, b) => a - b)) {
    sorted[rel][line] = [...result[rel][line]].sort();
  }
}
mkdirSync(directory, { recursive: true });
writeFileSync(join(directory, "linhas.json"), JSON.stringify(sorted, null, 2) + "\n");
