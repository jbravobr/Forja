"""The files of a new TypeScript project: ``package.json``, ``tsconfig.json``, a module and one
vitest test. The packages come from ``npm install``, which the environment runs (``setup``): the
tools find them under ``node_modules`` and download nothing themselves."""

from forja.core.adapters import GITATTRIBUTES, ProjectTemplate

GITIGNORE = """\
node_modules/
dist/
coverage/
.forja-coverage/
"""
SETUP = (("npm", "install", "--no-audit", "--no-fund"),)


def template(name: str) -> ProjectTemplate:
    return ProjectTemplate(
        {
            "package.json": f"""\
{{
  "name": "{name}",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {{
    "build": "tsc --noEmit -p .",
    "test": "vitest run"
  }},
  "devDependencies": {{
    "typescript": "^5.6.0",
    "vitest": "^5.0.5",
    "@vitest/coverage-v8": "^5.0.5"
  }}
}}
""",
            "tsconfig.json": """\
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
""",
            "src/index.ts": 'export const version = "0.1.0";\n',
            "src/index.test.ts": """\
import { expect, test } from "vitest";
import { version } from "./index";

test("the module has a version", () => {
  expect(version).toBe("0.1.0");
});
""",
            ".gitignore": GITIGNORE,
            ".gitattributes": GITATTRIBUTES,
        },
        SETUP,
    )
