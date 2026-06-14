import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import ts from "typescript";

const sourcePath = new URL("./agentRequestContext.ts", import.meta.url);
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-request-context-"));
const tempModulePath = path.join(tempDir, "agentRequestContext.mjs");
fs.writeFileSync(tempModulePath, compiled);

const {
  inferRequestedPageCount,
  resolveContentPlanPageCount,
} = await import(pathToFileURL(tempModulePath).href);

const perPageFeedback = "原来演讲的体量每一页的 ppt 内容要更有深度一些。每一页的内容可以再增加一点。";
const growthHackerBrief = `我要做一个完整的《增长黑客》这本书的 PPT。

你先去搜一下这本书的全文内容，然后再把它做成一个 PPT，要求如下：
1. 篇幅大概三四十页
2. 内容要详实，要让人家看完这个 PPT 就能大概知道《增长黑客》这本书讲了什么`;

assert.equal(
  inferRequestedPageCount(perPageFeedback),
  undefined,
  "per-page feedback must not be treated as a one-page deck request",
);
assert.equal(
  resolveContentPlanPageCount(perPageFeedback, 60),
  60,
  "Agent-supplied page count should survive per-page depth feedback",
);
assert.equal(
  resolveContentPlanPageCount("做成一页 PPT：恒河猴实验", 60),
  1,
  "explicit one-page deck requests should still be preserved",
);
assert.equal(
  inferRequestedPageCount(growthHackerBrief),
  40,
  "colloquial Chinese page ranges should survive numbered brief items",
);
assert.equal(
  resolveContentPlanPageCount(growthHackerBrief, undefined),
  40,
  "content-plan submission should pass the user's 30-40 page target",
);
