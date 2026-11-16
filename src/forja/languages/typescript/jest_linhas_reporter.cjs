// A Jest reporter for the tests of each line. When the run is over it reads the NDJSON rows that
// jest_linhas_setup.cjs wrote, maps each statement's compiled position to its TypeScript line (the
// project's own typescript compiles each source with the options of its tsconfig.json, and the
// source map says where each compiled position came from), and writes the tests of each line to
// .forja-test-lines/linhas.json with the ids the TAP reporter gives (file > describe > test).
const fs = require("node:fs");
const path = require("node:path");

class ForjaLinesReporter {
  constructor(globalConfig) {
    this.root = globalConfig.rootDir;
  }

  onRunComplete(_contexts, results) {
    const root = this.root;
    const directory = path.join(root, ".forja-test-lines");
    const rows = path.join(directory, "por-teste.ndjson");
    const ids = new Map();
    for (const file of results.testResults) {
      const rel = path.relative(root, file.testFilePath).split(path.sep).join("/");
      for (const test of file.testResults) {
        const id = [rel, ...test.ancestorTitles, test.title].join(" > ").replace(/\s+/g, " ");
        ids.set(`${rel} > ${test.fullName}`, id);
      }
    }
    const found = {};
    if (fs.existsSync(rows)) {
      const from = { paths: [root] };
      const ts = require(require.resolve("typescript", from));
      const { SourceMapConsumer } = require(require.resolve("source-map", from));
      const config = ts.getParsedCommandLineOfConfigFile(
        path.join(root, "tsconfig.json"),
        {},
        { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => {} },
      ).options;
      const consumers = new Map();
      const consumerFor = (rel) => {
        if (!consumers.has(rel)) {
          const fileName = path.join(root, rel);
          const out = ts.transpileModule(fs.readFileSync(fileName, "utf8"), {
            fileName,
            compilerOptions: { ...config, module: ts.ModuleKind.CommonJS, sourceMap: true, noEmit: false },
          });
          consumers.set(rel, new SourceMapConsumer(JSON.parse(out.sourceMapText)));
        }
        return consumers.get(rel);
      };
      for (const row of fs.readFileSync(rows, "utf8").split("\n").filter(Boolean)) {
        const { test, compiled } = JSON.parse(row);
        const id = ids.get(test);
        if (!id) continue;
        for (const [rel, positions] of Object.entries(compiled)) {
          const consumer = consumerFor(rel);
          for (const [line, column] of positions) {
            const original = consumer.originalPositionFor({ line, column });
            if (original.line === null) continue;
            const set = ((found[rel] ||= {})[original.line] ||= new Set());
            set.add(id);
          }
        }
      }
    }
    const plain = {};
    for (const rel of Object.keys(found).sort()) {
      plain[rel] = {};
      for (const line of Object.keys(found[rel]).map(Number).sort((a, b) => a - b)) {
        plain[rel][line] = [...found[rel][line]].sort();
      }
    }
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, "linhas.json"), JSON.stringify(plain, null, 2) + "\n");
  }
}

module.exports = ForjaLinesReporter;
