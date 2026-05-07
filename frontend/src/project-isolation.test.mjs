import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const sourcePath = join(import.meta.dirname, "App.tsx");
const source = readFileSync(sourcePath, "utf8");
const css = readFileSync(join(import.meta.dirname, "index.css"), "utf8");
const lines = source.split(/\r?\n/);

assert.match(source, /projectId\?: string;/, "ChatMessage must carry projectId ownership");
assert.match(
  source,
  /selectedProject && chatHistoryProjectId === selectedProject\.id[\s\S]*normalizeProjectChatHistory\(selectedProject\.id, roleChatMessages\)/,
  "visible chat messages must be filtered by the loaded project id"
);
assert.equal(
  (source.match(/if \(chatHistoryProjectId !== selectedProject\.id\) return;/g) || []).length,
  2,
  "content and visual persistence effects must both require project ownership"
);
assert.match(
  source,
  /const addSystemLog = \(content: string\) => \{[\s\S]*appendProjectChatMessage\(projectId, "content"[\s\S]*appendProjectChatMessage\(projectId, "visual"/,
  "system logs must go through project-scoped append"
);
assert.match(
  source,
  /const appendRequestMessage = \(message: ChatMessage[\s\S]*if \(!options\.allowStale && !isRequestCurrentGate\(\)\) return false;/,
  "request-scoped messages must be blocked once the gate is stale"
);
assert.match(
  source,
  /if \(!isRequestCurrentGate\(\)\) \{[\s\S]*pendingChatRef\.current = null;[\s\S]*return;[\s\S]*const frontendWillHandleAgentReply/,
  "stale Agent responses must be dropped before replies or actions are applied"
);
assert.match(
  source,
  /if \(\(!chatResultLooksValid\(result\) \|\| streamRetryReason\)[\s\S]*if \(!isRequestCurrentGate\(\)\) \{[\s\S]*pendingChatRef\.current = null;[\s\S]*return;/,
  "stale incomplete Agent streams must be dropped before retrying or warning"
);
assert.match(
  source,
  /if \(!chatResultLooksValid\(result\)\) \{[\s\S]*if \(!isRequestCurrentGate\(\)\) \{[\s\S]*pendingChatRef\.current = null;[\s\S]*return;[\s\S]*响应未返回完整结果/,
  "incomplete result warnings must only be shown for the current gate"
);
assert.match(
  source,
  /响应\(\?:未返回完整结果\|不完整，正在自动重试\)/,
  "old incomplete-response noise must be removed from persisted chat history"
);
assert.match(
  source,
  /chatInProgressRef\.current &&[\s\S]*activeChatProjectIdRef\.current !== projectId[\s\S]*activeChatRoleRef\.current !== currentAgentRoleRef\.current[\s\S]*activeChatGateRef\.current !== gateContextRef\.current\.gate[\s\S]*return;/,
  "active chat writes must not jump to another project, Agent role, or Gate revision"
);
assert.match(
  source,
  /const abortActiveChat = \(silent = true\)[\s\S]*silentChatAbortRef\.current = silent[\s\S]*pendingChatRef\.current = null;/,
  "programmatic chat aborts must be silent and clear pending retries"
);
assert.match(
  source,
  /const getPrototypeTargetSlides = \(explicitPageNums: number\[] = \[]\)[\s\S]*return slides\.slice\(0, Math\.min\(3, slides\.length\)\);/,
  "default prototype generation must target the first 3 pages, not the full deck"
);
assert.match(
  source,
  /case "start_prototype": \{[\s\S]*const prototypePageNums = getPrototypeTargetSlides\(pageNums\)\.map\(\(slide\) => slide\.page_num\);[\s\S]*handleStartGeneration\(true, true, prototypePageNums\);/,
  "prototype generation must pass the resolved sample page numbers"
);
assert.match(
  source,
  /case "resample_prototype": \{[\s\S]*const prototypePageNums = getPrototypeResampleTargetSlides\(pageNums\)\.map\(\(slide\) => slide\.page_num\);[\s\S]*handleStartGeneration\(true, true, prototypePageNums\);/,
  "prototype resampling must preserve the selected or already sampled pages"
);
assert.match(
  source,
  /disabled=\{isBusy \|\| chatLoading \|\| !canStartPrototypeGeneration\}/,
  "the visual-stage sample button must use prototype readiness, not full-deck readiness"
);
assert.match(
  source,
  /const canStartFullGeneration =\s*slides\.length > 0 &&\s*slides\.every\(slideHasPrompt\);/,
  "full generation readiness must check the whole deck"
);
assert.doesNotMatch(
  source,
  /visualInspector|pg-visual-inspector|pg-slide-grid-style-preview|selectedPromptTargets/,
  "visual design must use the slide detail editor and must not restyle cards as a fake style preview"
);
assert.doesNotMatch(
  css,
  /pg-visual-inspector|pg-slide-grid-style-preview/,
  "removed visual inspector/style-preview classes must not linger in CSS"
);

const allowedDirectSetContexts = [
  "const setActiveChatMessages",
  "const appendProjectChatMessage",
  "const updateProjectChatMessages",
  "loadedChatProjectIdRef.current !== selectedProject.id",
  "const handleCreate",
  "chatHistoryProjectIdRef.current = created.id",
];

const directSetPattern = /set(?:Content|Visual)ChatHistory\(/;
for (let index = 0; index < lines.length; index += 1) {
  if (!directSetPattern.test(lines[index])) continue;
  const context = lines.slice(Math.max(0, index - 25), Math.min(lines.length, index + 8)).join("\n");
  const allowed = allowedDirectSetContexts.some((needle) => context.includes(needle));
  assert.ok(
    allowed,
    `Direct chat history write at ${index + 1} must use appendProjectChatMessage/updateProjectChatMessages or project load/init`
  );
}

assert.doesNotMatch(
  source,
  /chatMessages\s*=[\s\S]{0,220}allowLegacy:\s*true/,
  "rendered chat must not accept legacy/unowned messages"
);
assert.doesNotMatch(
  source,
  /localStorage\.setItem\(`ppt_god_chat_(?:content|visual)_\$\{selectedProject\.id\}`,[\s\S]{0,180}allowLegacy:\s*true/,
  "persisted selected-project chat must not accept legacy/unowned messages"
);

console.log("project isolation tests passed");
