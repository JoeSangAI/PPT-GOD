import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const sourcePath = join(import.meta.dirname, "App.tsx");
const source = readFileSync(sourcePath, "utf8");
const css = readFileSync(join(import.meta.dirname, "index.css"), "utf8");
const workflow = readFileSync(join(import.meta.dirname, "workflow.ts"), "utf8");
const client = readFileSync(join(import.meta.dirname, "api/client.ts"), "utf8");
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
  /const addSystemLog = \(content: string(?:, attachments\?: ChatAttachment\[\])?\) => \{[\s\S]*appendProjectChatMessage\(projectId, "content"[\s\S]*appendProjectChatMessage\(projectId, "visual"/,
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
  /allowWhileChatLoading\?: boolean/,
  "chat-triggered gate actions must be able to run while the chat request is still resolving"
);
assert.match(
  source,
  /result\.action === "regenerate_plan"[\s\S]*appendRequestMessage[\s\S]*dispatchGateAction\([\s\S]*"generate_content_plan"[\s\S]*allowWhileChatLoading: true[\s\S]*source: "agent"/,
  "content-stage regenerate_plan must acknowledge the user and execute instead of being silently blocked by chatLoading"
);
assert.match(
  source,
  /result\.action === "regenerate_plan"[\s\S]*dispatchGateAction\([\s\S]*attachment_ids: attachmentIdsForRequest/,
  "Agent-triggered content-plan regeneration must carry the same attachment ids that informed the chat reply"
);
assert.match(
  source,
  /const notifyStartBlocked = \(message: string\)[\s\S]*没有启动重新生成：\$\{message\}[\s\S]*Content plan start latch check failed/,
  "content-plan regeneration must not silently die behind a stale local start latch"
);
assert.match(
  source,
  /const CONTENT_PLAN_START_LATCH_GRACE_MS[\s\S]*contentPlanStartingAtRef\.current = Date\.now\(\)/,
  "content-plan local start latch must be timestamped so stale latches can recover"
);
assert.match(
  client,
  /generateContentPlan\(projectId: string, topic\?: string, pageCount\?: number, attachmentIds\?: string\[\]\)[\s\S]*body\.attachment_ids = attachmentIds/,
  "content-plan API requests must support explicit attachment ids"
);
assert.match(
  source,
  /const getLatestComposerTextForSubmission = \(forcedMsg\?: string\)[\s\S]*isBriefStudioActive \? readBriefEditorValue\(\) : chatInput/,
  "Brief Studio submission must read the latest contenteditable value instead of relying only on stale React state"
);
assert.doesNotMatch(
  source,
  /正在解析上传内容/,
  "Brief Studio must not present document parsing as a blocking composer state"
);
assert.match(
  source,
  /const briefComposerSupportText = uploadingDoc[\s\S]*正在加入材料；你可以继续写 Brief[\s\S]*材料解析中；你可以继续补充 Brief/,
  "Brief Studio upload status must keep the writing entry visible while files are being prepared"
);
assert.match(
  source,
  /disabled=\{!selectedProject \|\| chatLoading \|\| isBusy \|\| uploadingDoc \|\| \(!chatInput\.trim\(\) && !hasBriefAttachments\)\}/,
  "Brief Studio must wait for the upload request to finish before starting content planning"
);
assert.match(
  source,
  /const userBrief = payload\?\.topic \|\| getLatestComposerTextForSubmission\(\);[\s\S]*const inferredPageCount = payload\?\.page_count \|\| inferRequestedPageCount\(userBrief\)/,
  "content-plan submission must infer explicit page-count goals from the submitted Brief"
);
assert.match(
  source,
  /function buildSubmittedBriefDisplayContent[\s\S]*本次要求：\\n\$\{brief\}/,
  "submitted Brief messages must show the actual request in the Agent sidebar"
);
assert.match(
  source,
  /currentSlides\.find\(\(s: Slide\) => s\.page_num === currentEditingSlide\.page_num\)/,
  "content-plan regeneration must rebind the active editor to the fresh slide with the same page number"
);
assert.match(
  workflow,
  /if \(gate === "content"\) \{[\s\S]*actions\.push\("generate_content_plan"\)/,
  "content-stage gate must allow content-plan regeneration before content is confirmed"
);
assert.match(
  workflow,
  /if \(state\.projectStatus !== "draft" && !state\.contentPlanConfirmed\) actions\.push\("confirm_content", "switch_to_visual"\)/,
  "content-stage gate must allow Agent handoff to visual to execute the same confirmation path as the button"
);
assert.doesNotMatch(
  source,
  /nextAction\.type === "switch_to_visual" && hasContentConfirmCta/,
  "chat handoff suggestions to visual must not be hidden when the main confirmation CTA exists"
);
assert.match(
  source,
  /const shouldRenderMessageNextAction = \(message: ChatMessage\) => \{[\s\S]*if \(!isMessageFromCurrentGate\(message\)\) return false;/,
  "stale gate-bound chat actions must be removed from the active task flow"
);
assert.doesNotMatch(
  source,
  /回退前|已失效|当前步骤不能执行|回滚到此消息/,
  "current user-facing UI must not expose stale rollback/internal-state wording"
);
assert.match(
  source,
  /isWorkflowTransitionMessage\(msg\)[\s\S]*msg\.role === "system"[\s\S]*!msg\.gate[\s\S]*!isMessageFromCurrentGate\(msg\)/,
  "workflow transition notes must not reappear as current guidance after reload"
);
assert.match(
  source,
  /interface GateActionResult[\s\S]*ok: boolean[\s\S]*reason\?:/,
  "gate actions must return an execution result instead of failing silently"
);
assert.match(
  source,
  /const reportBlockedAction = \([\s\S]*options\.source === "agent"[\s\S]*appendProjectChatMessage[\s\S]*return \{ ok: false, reason, message \};/,
  "blocked agent actions must produce visible chat feedback"
);
assert.match(
  source,
  /const startContentPlanPoll = async \([\s\S]*Promise<GateActionResult>[\s\S]*正在读取当前页面，准备生成内容规划[\s\S]*return \{ ok: true, runId: result\?\.run\?\.id \};/,
  "content-plan generation must show immediate status and report startup success"
);
assert.match(
  source,
  /contentPlanStartingProjectRef\.current === projectId[\s\S]*return \{ ok: false, reason: "busy"/,
  "content-plan generation must have a synchronous guard against rapid duplicate submissions"
);
assert.match(
  source,
  /contentPlanStartingProjectRef\.current = projectId[\s\S]*generateContentPlan\(projectId, topic, pageCount/,
  "content-plan duplicate-submit guard must be set before the API request starts"
);
assert.match(
  source,
  /\["failed", "stale", "cancelled"\]\.includes\(String\(workflow\.last_run\.status \|\| ""\)\)/,
  "content-plan polling must surface stale/cancelled runs as visible failures"
);
assert.match(
  source,
  /if \(currentSlides\.length > 0 && currentSlideIds !== previousSlideIds\) \{[\s\S]*options\?\.onStarted\?\.\(\);[\s\S]*setContentPlanSnapshot/,
  "Brief Studio draft should only be cleared after new content-plan slides actually exist"
);
assert.match(
  source,
  /if \(isBusy \|\| chatLoading\) \{[\s\S]*当前还有任务或消息在处理中[\s\S]*return;/,
  "manual next actions must show why they cannot run instead of returning silently"
);
assert.match(
  source,
  /result\.action === "forward_to_visual"[\s\S]*收到，正在请视觉总监介入[\s\S]*dispatchGateAction\("switch_to_visual"[\s\S]*allowWhileChatLoading: true[\s\S]*source: "agent"/,
  "Agent visual handoff must execute the handoff action and avoid claiming it already switched before the action runs"
);
assert.match(
  source,
  /没有找到第 \$\{pageNums\.join\(", "\)\} 页，未生成新的画面方案/,
  "page-targeted visual rerolls must report when no target page was changed"
);
assert.match(
  source,
  /const buildCrossStageContext = \(targetRole: "content" \| "visual" \| "finetune"\)[\s\S]*summarizeStageMessages\(contentChatHistory, "内容阶段"\)/,
  "visual-stage requests must inherit user requirements from content-stage chat"
);
assert.match(
  source,
  /withCrossStageContext\(pageContext, requestAgentRole\)[\s\S]*chatWithAgentStream\(\s*requestProjectId,\s*[\s\S]*history,\s*ctrl\.signal,\s*effectivePageContext,\s*requestAgentRole/,
  "Agent chat requests must include cross-stage context in page_context"
);
assert.match(
  source,
  /const isVisualRelevantStageContext = \([\s\S]*用户\(\?:在第\\s\*\\d\+\\s\*页\[前后\]插入了新页面\|删除了第\\s\*\\d\+\\s\*页\|调整了页面顺序\)/,
  "visual handoff context must filter structural content-operation logs"
);
assert.match(
  source,
  /function buildVisualStyleGenerationContext\([\s\S]*用户：\$\{content\}[\s\S]*视觉总监：\$\{content\}/,
  "style proposal generation must preserve recent visual chat requirements instead of only sending the fixed trigger text"
);
assert.match(
  source,
  /const isBackendStyleGenerationRequest = isVisualStyleGenerationMessage\(userMsg\)[\s\S]*const styleGenerationContext = buildVisualStyleGenerationContext\([\s\S]*history,[\s\S]*userMsg,[\s\S]*buildCrossStageContext\("visual"\)/,
  "visual style generation must build backend context from the same chat that produced the visible reply"
);
assert.match(
  source,
  /const fallbackProposal = !isBackendStyleGenerationRequest && !canStartBackendStyleProposal && !proposalFromAgent && fallbackBaseStyle/,
  "visual style-stage adjustments must use backend regeneration instead of locally falling back to the previous proposal"
);
assert.match(
  source,
  /generateStyleProposals\(requestProjectId, shouldForceStyleProposal, styleGenerationContext\)/,
  "backend style generation must receive visual chat requirements"
);
assert.match(
  source,
  /failed to fetch\|networkerror\|network request failed\|load failed/,
  "raw browser network errors must be translated before showing generation failures"
);
assert.match(
  source,
  /const resolveWorkflowFailureMessage = async \([\s\S]*fetchWorkflowStatus\(projectId\)[\s\S]*\["failed", "stale", "cancelled"\]\.includes\(String\(run\.status \|\| ""\)\)/,
  "generation failure handlers must re-check workflow state before falling back to local fetch errors"
);
assert.match(
  source,
  /风格提案生成失败："\s*\+\s*errorMessage[\s\S]*视觉方向生成失败："\s*\+\s*errorMessage/,
  "both chat-triggered and button-triggered style generation failures must use resolved workflow errors"
);
assert.doesNotMatch(
  source,
  /handleSendChat\(fakeUserMsg\)/,
  "style proposal buttons must call explicit generation actions instead of sending magic chat messages"
);
assert.match(
  source,
  /case "generate_style_proposals": \{[\s\S]*buildVisualStyleGenerationContext\([\s\S]*generateStyleProposals\(\s*currentProject\.id,[\s\S]*styleGenerationContext/,
  "style proposal buttons must preserve visual requirements while using the explicit generation API"
);
assert.match(
  client,
  /JSON\.stringify\(\{ user_description: trimmedDescription \}\)/,
  "style proposal API client must send chat-derived user style requirements"
);
assert.match(
  source,
  /const addSystemLog = \(content: string(?:, attachments\?: ChatAttachment\[\])?\) => \{[\s\S]*appendProjectChatMessage\(projectId, "content"[\s\S]*if \(isVisualRelevantStageContext\(content, "system"\)\) \{[\s\S]*appendProjectChatMessage\(projectId, "visual"/,
  "system logs must only enter the visual Agent when they affect visual decisions"
);
assert.match(
  source,
  /msg\.role === "system" && currentAgentRole === "visual"[\s\S]*getVisualSystemMessageContent\(msg\.content\)[\s\S]*!systemContentForVisual[\s\S]*return null;/,
  "visual Agent rendering must hide irrelevant historical system logs"
);
assert.match(
  source,
  /const defaultPrototypePageNumsForSlides = \(slides: Slide\[]\): number\[] => \{[\s\S]*PROTOTYPE_FAMILY_ORDER[\s\S]*const getPrototypeTargetSlides = \(explicitPageNums: number\[] = \[]\)[\s\S]*prototypeSelectionTouched \? Array\.from\(selectedPages\) : defaultPrototypePageNums[\s\S]*const defaultPrototypePageNums = defaultPrototypePageNumsForSlides\(slides\);/,
  "default prototype generation must target representative seed pages, not the full deck"
);
assert.match(
  source,
  /function normalizeProjectsForActiveSelection\(projects: Project\[], activeProjectId: string \| null\)[\s\S]*clearProjectNotification\(project\)/,
  "project lists must normalize unread notifications for the active project"
);
assert.match(
  source,
  /function projectStyleLabel\(project: Project\)[\s\S]*project\.selected_style\?\.name[\s\S]*project\.style_proposal\?\.proposals\?\.\[0\]\?\.name[\s\S]*默认风格/,
  "project sidebar style labels must reflect the generated style proposal before final style selection"
);
assert.match(
  source,
  /statusLabel\[p\.status\][\s\S]*projectStyleLabel\(p\)/,
  "project sidebar must use the generated proposal label instead of falling back to 默认风格"
);
assert.match(
  source,
  /const loadProjects = async \(\) => \{[\s\S]*activeProjectHadUnread[\s\S]*normalizeProjectsForActiveSelection\(data, currentSelectedId\)[\s\S]*markActiveProjectNotificationRead\(currentSelectedId\)/,
  "project polling must treat notifications for the open project as already seen and sync read state"
);
assert.match(
  source,
  /p\.has_unread_notification && selectedProject\?\.id !== p\.id/,
  "the sidebar unread dot must only render for projects outside the current page"
);
assert.match(
  source,
  /const isPrototypeRunActive = Boolean\(hasActiveRun && activeRun\?\.kind === "prototype_generation"\);[\s\S]*disabled=\{!canEditPrototypeSelection\}/,
  "prototype selection checkboxes must be locked while a prototype run is active"
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
assert.match(
  source,
  /function visualStrategyText[\s\S]*visual_strategy/,
  "style proposals must surface the deck-level visual background strategy"
);
assert.match(
  source,
  /proposalDecisionField[\s\S]*best_for[\s\S]*tradeoff[\s\S]*visual_focus/,
  "style proposal cards must surface decision criteria before shared visual strategy"
);
assert.match(
  css,
  /pg-style-dock-decision/,
  "style proposal cards need a dedicated decision summary treatment"
);
assert.match(
  source,
  /整体基底/,
  "selected style summary must show the visual background strategy so users can revise it"
);
assert.match(
  source,
  /const wantsLight =[\s\S]*不喜欢黑紫[\s\S]*const wantsDarkTech = !wantsLight/,
  "fallback style adjustments must not treat '不喜欢黑紫' as a dark-style request"
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
  "const ensureContentGreetingIfNeeded",
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
