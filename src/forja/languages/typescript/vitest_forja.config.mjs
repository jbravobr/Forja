// Forja's configuration for Vitest, passed with --config from outside the project. It loads the
// project's own configuration (if any) and adds what the Forja needs: the order of the files (from
// FORJA_FILE_ORDER, the files of the tests asked for first), and, when FORJA_TEST_LINES is 1, the
// per-test coverage of vitest_setup.mjs. Imports resolve from the project's node_modules, so nothing
// is installed next to this file.
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = process.cwd();
const { mergeConfig } = await import(pathToFileURL(join(root, "node_modules/vitest/dist/config.js")).href);
const { BaseSequencer } = await import(pathToFileURL(join(root, "node_modules/vitest/dist/node.js")).href);
const vite = await import(
  pathToFileURL(createRequire(join(root, "node_modules/vitest/package.json")).resolve("vite")).href
);
const here = fileURLToPath(new URL(".", import.meta.url));

const userFile = ["vitest.config.ts", "vitest.config.mts", "vitest.config.js", "vitest.config.mjs"]
  .map((name) => join(root, name))
  .find((file) => existsSync(file));
const loaded = userFile
  ? await vite.loadConfigFromFile({ command: "serve", mode: "test" }, userFile, root)
  : null;
const user = loaded?.config ?? {};

class ForjaSequencer extends BaseSequencer {
  async sort(specs) {
    const order = (process.env.FORJA_FILE_ORDER || "").split(",").filter(Boolean);
    const rank = (spec) => {
      const at = order.indexOf(relative(root, spec.moduleId).split(sep).join("/"));
      return at === -1 ? order.length : at;
    };
    return [...specs].sort((a, b) => rank(a) - rank(b));
  }
}

const testLines = process.env.FORJA_TEST_LINES === "1";

export default mergeConfig(user, {
  root,
  test: {
    sequence: { sequencer: ForjaSequencer },
    ...(testLines ? { setupFiles: [pathToFileURL(join(here, "vitest_setup.mjs")).href] } : {}),
  },
});
