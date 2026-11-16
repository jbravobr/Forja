// The build of a JavaScript project, which has no compiler: Node checks the syntax of every file
// it would load itself (.js, .mjs, .cjs) with `node --check`, one process per file, as many at a
// time as there are processors. The directories named as arguments, and the hidden ones, are left
// out. A file that does not parse is one line, `<path>:<line>: <error>`, with the path relative to
// the directory this runs in; the exit code is 1 when any file fails and 0 otherwise.
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const SUFFIXES = [".js", ".mjs", ".cjs"];
const skipped = new Set(process.argv.slice(2));

function files(directory, found) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (!skipped.has(entry.name) && !entry.name.startsWith(".")) files(full, found);
    } else if (entry.isFile() && SUFFIXES.includes(path.extname(entry.name))) {
      found.push(full);
    }
  }
  return found;
}

function check(file) {
  return new Promise((resolve) => {
    // the whole path: a file named like an option would be read as one
    execFile(process.execPath, ["--check", path.resolve(file)], (error, _stdout, stderr) => {
      if (!error) return resolve(null);
      // Node prints `<file>:<line>`, the source line, a caret and then `SyntaxError: <what>`
      const lines = String(stderr).split(/\r?\n/);
      const where = /:(\d+)$/.exec(lines[0] || "");
      const what = lines.find((line) => /^\w*Error\b/.test(line)) || "Error: did not parse";
      const name = file.split(path.sep).join("/");
      resolve(`${name}:${where ? where[1] : 0}: ${what}`);
    });
  });
}

async function main() {
  const queue = files(".", []).sort();
  const failures = [];
  const worker = async () => {
    for (let file = queue.shift(); file !== undefined; file = queue.shift()) {
      const failure = await check(file);
      if (failure) failures.push(failure);
    }
  };
  await Promise.all(Array.from({ length: os.cpus().length || 1 }, worker));
  for (const line of failures.sort()) console.log(line);
  process.exitCode = failures.length ? 1 : 0;
}

main();
