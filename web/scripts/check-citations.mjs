import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

const web = resolve(import.meta.dirname, "..");
const out = mkdtempSync(join(web, ".citation-test-"));
const tsc = join(web, "node_modules", "typescript", "bin", "tsc");

try {
  execFileSync(
    process.execPath,
    [
      tsc,
      "src/components/Badges.tsx",
      "--ignoreConfig",
      "--jsx",
      "react-jsx",
      "--module",
      "commonjs",
      "--target",
      "es2022",
      "--moduleResolution",
      "bundler",
      "--skipLibCheck",
      "--outDir",
      out,
    ],
    { cwd: web, stdio: "pipe" },
  );
  writeFileSync(join(out, "package.json"), JSON.stringify({ type: "commonjs" }));
  const require = createRequire(import.meta.url);
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  const { CitationChips } = require(join(out, "components", "Badges.js"));

  const citations = [
    {
      kind: "document",
      ref: "d1",
      source_id: "returns-policy",
      span_id: null,
      audit_id: null,
      snippet: "first snippet",
    },
    {
      kind: "document",
      ref: "d3",
      source_id: "returns-policy",
      span_id: null,
      audit_id: null,
      snippet: "second snippet",
    },
    {
      kind: "tool",
      ref: "t1",
      source_id: "orders_summary@1",
      span_id: null,
      audit_id: "a1",
      snippet: "8 rows",
    },
  ];
  const html = renderToStaticMarkup(
    React.createElement(CitationChips, {
      runId: "run",
      tenantId: "default",
      citations,
    }),
  );

  const failures = [];
  const docChips = (html.match(/class="chip chip-document"/g) ?? []).length;
  if (docChips !== 1) failures.push(`expected 1 document chip, got ${docChips}`);
  if (!html.includes("doc d1, d3")) failures.push("document refs were not grouped");
  if (!html.includes("tool t1")) failures.push("tool chip missing");
  if (failures.length > 0) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  } else {
    console.log("citation grouping checks passed");
  }
} finally {
  rmSync(out, { recursive: true, force: true });
}
