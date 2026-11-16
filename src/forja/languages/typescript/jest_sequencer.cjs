// Runs the test files in the order given by FORJA_FILE_ORDER (comma-separated paths relative to the
// project), the files not listed last. Passed with --testSequencer and -i, so the order holds.
const path = require("node:path");
const Base = require(require.resolve("@jest/test-sequencer", { paths: [process.cwd()] })).default;

class OrderSequencer extends Base {
  sort(tests) {
    const order = (process.env.FORJA_FILE_ORDER || "").split(",").filter(Boolean);
    const rank = (test) => {
      const rel = path.relative(test.context.config.rootDir, test.path).split(path.sep).join("/");
      const at = order.indexOf(rel);
      return at === -1 ? order.length : at;
    };
    return [...tests].sort((a, b) => rank(a) - rank(b));
  }
}

module.exports = OrderSequencer;
