import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const sourcePath = join(import.meta.dirname, "App.tsx");
const source = readFileSync(sourcePath, "utf8");
const css = readFileSync(join(import.meta.dirname, "index.css"), "utf8");
const workflow = readFileSync(join(import.meta.dirname, "workflow.ts"), "utf8");
const workflowHook = readFileSync(join(import.meta.dirname, "hooks/useProjectWorkflow.ts"), "utf8");
const client = readFileSync(join(import.meta.dirname, "api/client.ts"), "utf8");
const lines = source.split(/\r?\n/);

assert.match(
  css,
  /Login density[\s\S]*\.pg-auth-v2 \.pg-auth-shell\s*\{[\s\S]*max-width:\s*1120px;[\s\S]*grid-template-columns:\s*minmax\(360px, 520px\) minmax\(320px, 400px\);[\s\S]*padding:\s*28px 32px;/,
  "public login screen must default to the compact production workspace ratio"
);
assert.match(
  css,
  /Login density[\s\S]*\.pg-auth-v2 \.pg-auth-card-v2\s*\{[\s\S]*max-width:\s*400px;[\s\S]*padding:\s*22px;/,
  "public login card must stay compact instead of reverting to the oversized default"
);
assert.match(
  css,
  /Login density[\s\S]*\.pg-auth-input\s*\{[\s\S]*height:\s*40px;[\s\S]*font-size:\s*13\.5px;/,
  "public login form controls must match the denser production UI scale"
);

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
  /const appendRequestMessage = \(message: ChatMessage[\s\S]*if \(!options\.allowStale && !isRequestExecutionAllowed\(\)\) return false;/,
  "request-scoped messages must be blocked only when the original request can no longer execute"
);
assert.match(
  source,
  /if \(!isRequestExecutionAllowed\(\)\) \{[\s\S]*clearPendingChatRequest\(requestProjectId\);[\s\S]*return;[\s\S]*const frontendWillHandleAgentReply/,
  "stale same-project Agent responses must be dropped before replies or actions are applied"
);
assert.match(
  source,
  /if \(\(!chatResultLooksValid\(result\) \|\| streamRetryReason\)[\s\S]*if \(!isRequestExecutionAllowed\(\)\) \{[\s\S]*clearPendingChatRequest\(requestProjectId\);[\s\S]*return;/,
  "same-project stale incomplete Agent streams must be dropped before retrying or warning"
);
assert.match(
  source,
  /if \(!chatResultLooksValid\(result\)\) \{[\s\S]*if \(!isRequestExecutionAllowed\(\)\) \{[\s\S]*clearPendingChatRequest\(requestProjectId\);[\s\S]*return;[\s\S]*响应未返回完整结果/,
  "incomplete result warnings must only be shown while the original request can still execute"
);
assert.match(
  source,
  /if \(ctrl\.signal\.aborted\) \{[\s\S]*已停止生成[\s\S]*clearPendingChatRequest\(requestProjectId\);[\s\S]*return;[\s\S]*if \(!chatResultLooksValid\(result\)\) \{/,
  "user-stopped chat streams must return before the incomplete-response fallback"
);
assert.match(
  source,
  /响应\(\?:未返回完整结果\|不完整，正在自动重试\)/,
  "old incomplete-response noise must be removed from persisted chat history"
);
assert.match(
  source,
  /chatInProgressRef\.current &&[\s\S]*activeChatProjectIdRef\.current !== projectId[\s\S]*activeChatRoleRef\.current !== role[\s\S]*activeChatGateRef\.current !== gateContextRef\.current\.gate[\s\S]*return;/,
  "active chat writes must not jump to another project, Agent role, or Gate revision"
);
assert.match(
  source,
  /const abortActiveChat = \(silent = true\)[\s\S]*silentChatAbortRef\.current = silent[\s\S]*clearPendingChatRequest\(\);/,
  "programmatic chat aborts must be silent and clear pending retries"
);
assert.match(
  source,
  /const getPendingChatStorageKey[\s\S]*readPendingChat[\s\S]*writePendingChat/,
  "in-flight Agent requests must be persisted so tab switches or reloads can recover them"
);
assert.match(
  source,
  /const appendProjectChatMessage[\s\S]*const nextStored = appendStoredChatMessage\(projectId, role, normalized\);[\s\S]*set(?:Content|Visual)ChatHistory\(nextStored\)/,
  "active project chat appends must synchronously persist instead of waiting for a later React effect"
);
assert.match(
  source,
  /const setActiveChatMessages[\s\S]*updateRoleChatMessages\([\s\S]*projectId,[\s\S]*role,[\s\S]*slideId[\s\S]*\);/,
  "manual active chat updates must use the same synchronous storage path as background updates"
);
assert.match(
  source,
  /const updateFinetuneChatMessages[\s\S]*updateRoleChatMessages\(projectId, "finetune", updater, slideId\)/,
  "single-page finetune messages must persist by project and slide instead of living only in React state"
);
assert.match(
  source,
  /function getAgentStatusMessageKey[\s\S]*message\.runId && !message\.loading[\s\S]*content-plan-ready[\s\S]*style-ready[\s\S]*visual-prompts-ready[\s\S]*generation-result/,
  "Agent status replies must have generic semantic keys across content and visual stages"
);
assert.match(
  source,
  /function upsertAgentStatusMessage[\s\S]*message\.runId && item\.loading && item\.runId === message\.runId[\s\S]*getAgentStatusMessageKey\(item\) !== key/,
  "Agent status replies must replace stale status rows instead of stacking duplicates"
);
assert.match(
  source,
  /locallyHandledRunIdsRef\.current\.add\(String\(result\.run\.id\)\)[\s\S]*!activeContentPlan && contentPlanSucceeded[\s\S]*upsertAgentStatusMessage/,
  "content-plan polling must own its run completion and only announce completion after the backend run succeeds"
);
assert.match(
  source,
  /locallyHandledRunIdsRef\.current\.has\(prevRun\.runId\)[\s\S]*!\(m\.loading && m\.runId === prevRun\.runId\)/,
  "locally handled run cleanup must remove loading rows without deleting the final completion reply"
);
assert.match(
  source,
  /const pageReferenceRoute = \(ref: any\): AssetRoute => \{[\s\S]*mode === "original"[\s\S]*return "overlay"/,
  "page reference assets saved as original must reopen as precise paste instead of defaulting to smart blend"
);
assert.match(
  source,
  /function proposalColorValue\(color: any\)[\s\S]*#\?\(\(\?:\[0-9a-fA-F\]\{3\}\)\{1,2\}\)/,
  "style proposal swatches must render six-digit hex values even when the backend returns them without #"
);
assert.match(
  source,
  /const slideProjectMaterialItems = visualAssetIdsForSlide\(slide\)[\s\S]*projectVisualAssetById\.get\(id\)[\s\S]*slideProjectMaterialItems\.length > 0[\s\S]*slideProjectMaterialItems\.map/,
  "global slide cards must show project-level visual assets selected for the slide"
);
assert.match(
  source,
  /onImageClick=\{\(url\) => \{[\s\S]*visualAssetIdsForSlide\(editingSlide\)[\s\S]*projectVisualAssetById\.get\(id\)[\s\S]*setGalleryModal\(\{ urls: galleryUrls/,
  "single-slide reference preview must include project-level visual assets selected for the slide"
);
assert.match(
  source,
  /const clearLegacyChatStorageIfNeeded =?\s*function clearLegacyChatStorageIfNeeded|function clearLegacyChatStorageIfNeeded/,
  "chat storage schema handling must be centralized"
);
assert.match(
  source,
  /const clearTransientProjectState[\s\S]*setReferenceImages\(\[\]\);[\s\S]*setDocuments\(\[\]\);[\s\S]*setTemplatePages\(\[\]\);/,
  "project switches must clear uploaded document state before loading the next project"
);
assert.match(
  source,
  /const canApplyProjectLoadResult = \(projectId: string\) =>[\s\S]*selectedProjectIdRef\.current === projectId[\s\S]*loadingProjectIdRef\.current === projectId/,
  "project data loaders must require both the current project and the current load owner before painting visible state"
);
assert.match(
  source,
  /const clearOperatingProject = \(projectId: string\) =>[\s\S]*setOperatingProjectId\(\(current\) => current === projectId \? null : current\);/,
  "async project operations must only clear their own busy lock"
);
assert.match(
  source,
  /const loadSlides = async \(projectId: string\) => \{[\s\S]*if \(!canApplyProjectLoadResult\(projectId\)\) return slidesCacheRef\.current\[projectId\] \|\| \[\];[\s\S]*setSlidesProjectId\(projectId\);[\s\S]*setSlides\(data\);/,
  "slow slide responses from a previous project must not paint into the active project"
);
for (const operationName of ["restoreSlidesToBackend", "handleGeneratePrompts", "handleStartGeneration"]) {
  assert.match(
    source,
    new RegExp(`const ${operationName} = async[\\s\\S]*finally \\{[\\s\\S]*clearOperatingProject\\(projectId\\);`),
    `${operationName} must not clear a newer project's busy lock`
  );
}
for (const [loaderName, setterName] of [
  ["loadStatus", "setProjectStatus"],
  ["loadReferenceImages", "setReferenceImages"],
  ["loadDocuments", "setDocuments"],
  ["loadTemplatePages", "setTemplatePages"],
]) {
  assert.match(
    source,
    new RegExp(`const ${loaderName} = async \\(projectId: string\\) => \\{[\\s\\S]*if \\(!canApplyProjectLoadResult\\(projectId\\)\\) return;[\\s\\S]*${setterName}\\(`),
    `${loaderName} must ignore stale project responses before ${setterName}`
  );
}
assert.match(
  source,
  /if \(selectedProject\) \{[\s\S]*setReferenceImages\(\[\]\);[\s\S]*setDocuments\(\[\]\);[\s\S]*loadDocuments\(selectedProject\.id\);/,
  "selected project hydration must start with empty document state and then load only that project"
);
assert.match(
  source,
  /if \(created\) \{[\s\S]*clearTransientProjectState\(created\.id\);[\s\S]*setSelectedProject\(normalizedCreated\);/,
  "newly created projects must reset transient state before becoming active"
);
assert.doesNotMatch(
  source,
  /function clearLegacyChatStorageIfNeeded\(\) \{[\s\S]*keysToRemove[\s\S]*removeItem\(key\)/,
  "schema checks must not wipe project chat history as a recovery shortcut"
);
assert.match(
  source,
  /pendingChatRef\.current \|\| \(projectId \? restoreStoredPendingChatForProject\(projectId\) : null\)[\s\S]*const initialRecoveryTimer = window\.setTimeout\(recoverPendingChat, 600\)/,
  "pending Agent requests must be restored on focus and initial project load"
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
  /generateContentPlan\(projectId: string, topic\?: string, pageCount\?: number, attachmentIds\?: string\[\][^)]*\)[\s\S]*body\.attachment_ids = attachmentIds/,
  "content-plan API requests must support explicit attachment ids"
);
const removedEditableApiPattern = new RegExp(
  [
    ["editable", "-pptx"].join(""),
    ["download", "editable"].join("-"),
    ["Editable", "Pptx"].join(""),
  ].join("|")
);
const removedEditableControlsPattern = new RegExp(
  [
    ["下载可编辑", "版 PPTX"].join(""),
    "handle" + ["Editable", "Pptx"].join("") + "Export",
    ["pg", "editable", "export"].join("-"),
  ].join("|")
);
const removedEditableWorkflowPattern = new RegExp(
  [
    ["editable", "_pptx"].join(""),
    ["可编辑", "版"].join(""),
  ].join("|")
);
assert.doesNotMatch(client, removedEditableApiPattern, "editable PPTX export API must not remain in the client");
assert.match(
  client,
  /export interface FinetuneRegion[\s\S]*bbox:\s*\{[\s\S]*x:\s*number[\s\S]*width:\s*number[\s\S]*height:\s*number/,
  "single-slide finetune API must define selectable edit regions"
);
assert.match(
  client,
  /finetuneSlide\(projectId: string, slideId: string, instruction: string, attachmentIds\?: string\[\], regions\?: FinetuneRegion\[\]\)[\s\S]*regions:\s*regions \|\| \[\]/,
  "single-slide finetune API must send selected regions with the edit request"
);
assert.match(
  source,
  /interface FinetuneRegion[\s\S]*bbox:\s*\{[\s\S]*x:\s*number[\s\S]*width:\s*number[\s\S]*height:\s*number/,
  "single-slide finetune state must store normalized edit regions"
);
assert.match(
  source,
  /function FinetuneRegionSelector[\s\S]*onAddRegion[\s\S]*pg-finetune-region-box/,
  "single-slide finetune preview must support drawing selected edit regions"
);
assert.doesNotMatch(
  source,
  /pg-finetune-region-overlay-actions/,
  "single-slide finetune controls must not overlay buttons on top of the slide image"
);
assert.match(
  source,
  /is-single-slide-editing/,
  "single-slide editing must expose a root layout state for compact workbench chrome"
);
assert.match(
  source,
  /showFinetuneRegionControls\s*=\s*Boolean\([\s\S]*slide\.image_path[\s\S]*onToggleFinetuneRegionSelection[\s\S]*finetuneRegions\.length/,
  "single-slide editor region selection must only appear when the slide already has an image to select"
);
assert.match(
  source,
  /pg-single-region-tool[\s\S]*框选修改/,
  "single-slide finetune region selection must use explicit copy in the editor toolbar instead of covering the image"
);
assert.match(
  source,
  /pg-single-undo-redo[\s\S]*aria-label="撤销"[\s\S]*<svg[\s\S]*aria-label="重做"[\s\S]*<svg/,
  "single-slide undo and redo controls must use a WPS-style icon pill instead of font glyph arrows"
);
assert.match(
  css,
  /\.pg-single-slide-nav\s*\{[\s\S]*--pg-single-nav-height[\s\S]*--pg-single-nav-shell-bg/,
  "single-slide editor nav must define shared WPS-style toolbar tokens"
);
assert.match(
  css,
  /\.pg-single-nav-back,\s*[\r\n]+\.pg-single-page-control,\s*[\r\n]+\.pg-single-tool-group,\s*[\r\n]+\.pg-single-save-group\s*\{[\s\S]*min-height:\s*var\(--pg-single-nav-height\)[\s\S]*background:\s*var\(--pg-single-nav-shell-bg\)/,
  "single-slide editor nav groups must share the same pill shell instead of mixed button styles"
);
assert.doesNotMatch(
  css,
  /\.pg-single-region-tool\s*\{[\s\S]*rgba\(255,\s*247,\s*237/,
  "region selection must use the same neutral toolbar shell and reserve red for active state"
);
assert.match(
  source,
  /const renderSlidePreview = \(\)[\s\S]*\{renderSlidePreview\(\)\}[\s\S]*\/\* 标题 \*\//,
  "single-slide editor must put the slide preview before the text editing fields"
);
assert.match(
  source,
  /openFinetuneRegionMode[\s\S]*scrollFinetunePreviewIntoView/,
  "right-side finetune target card must be able to open and scroll to region selection"
);
assert.match(
  source,
  /hasFinetuneImage\s*&&\s*\([\s\S]*pg-finetune-scope-control[\s\S]*修改范围[\s\S]*整页修改[\s\S]*框选局部/,
  "right-side finetune target card must explain the region mechanism as a modification-scope choice"
);
assert.match(
  source,
  /hasFinetuneImage\s*&&\s*\([\s\S]*pg-finetune-region-target-button[\s\S]*框选局部/,
  "right-side finetune target card must expose local selection only after an image exists"
);
assert.match(
  source,
  /selectedFinetuneSlides[\s\S]*finetunePanelScope[\s\S]*selected_slides/,
  "right-side finetune target card must derive page range before deciding whether local selection is available"
);
assert.match(
  source,
  /finetunePanelScope === "selected_slides"[\s\S]*已选 \{selectedFinetuneSlides\.length\} 页[\s\S]*同一要求会应用到这些页面/,
  "right-side finetune target card must summarize multi-page scope in user terms"
);
assert.match(
  source,
  /singleFinetuneSlide[\s\S]*hasFinetuneImage[\s\S]*pg-finetune-scope-control/,
  "right-side finetune target card must keep local selection controls scoped to one generated slide"
);
assert.match(
  source,
  /pg-finetune-reference-button[\s\S]*加参考图/,
  "right-side finetune target card must label the reference-image action instead of showing only a plus icon"
);
assert.match(
  css,
  /\.pg-app\.is-single-slide-editing[\s\S]*\.pg-workflow[\s\S]*\.pg-workbench-modulebar[\s\S]*\.pg-style-bar/,
  "single-slide editing must compact project-level status, material, and style bars"
);
assert.match(
  source,
  /finetuneSlide\(selectedProject\.id, targetSlide\.id, userMsg, finetuneAttachments\.map\(\(a\) => a\.id\), finetuneRegions\)/,
  "single-slide finetune requests must include selected regions from the current target slide"
);
assert.match(
  source,
  /下载规划 MD/,
  "download actions should use consistent labels for planning MD and image PPTX"
);
assert.match(source, /下载图片版 PPTX/, "image PPTX download action should be clearly labeled");
assert.doesNotMatch(source, removedEditableControlsPattern, "editable PPTX export controls must not remain in the app");
assert.doesNotMatch(workflow, removedEditableWorkflowPattern, "editable PPTX export must not remain in workflow copy or steps");
assert.doesNotMatch(css, removedEditableControlsPattern, "editable PPTX export styles must not remain");
assert.match(
  css,
  /\.pg-topbar\.pg-project-header\s*\{[\s\S]*z-index:\s*80;[\s\S]*overflow:\s*visible;/,
  "editable PPTX export menu must render above workflow and style bands instead of being covered"
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
  /const userBrief = payload\?\.topic \|\| getLatestComposerTextForSubmission\(\);[\s\S]*const inferredPageCount = resolveContentPlanPageCount\(userBrief, payload\?\.page_count\)/,
  "content-plan submission must infer explicit page-count goals and ignore tiny unprompted Agent estimates"
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
  /const \[slidesRedoHistory,\s*setSlidesRedoHistory\]/,
  "deck-level rollback must keep redo snapshots separately from the undo stack"
);
assert.match(
  source,
  /const canGlobalUndo = slidesHistory\.length > 0;/,
  "first Agent content mutation must immediately enable rollback to the previous deck snapshot"
);
assert.match(
  source,
  /回退上一步/,
  "planning UI must expose a user-facing rollback button"
);
assert.match(
  source,
  /const previousSlides = await loadSlides\(projectId\);[\s\S]*if \(previousSlides\.length > 0\) pushSlidesHistory\(previousSlides\);[\s\S]*generateContentPlan/,
  "Agent-triggered content-plan regeneration must save the current deck before replacing slides"
);
assert.match(
  source,
  /restoreSlidesToBackend[\s\S]*await fetchSlides\(projectId\)[\s\S]*updateSlideContent\(projectId, targetPageNum, targetContent\)[\s\S]*createSlide\(projectId, targetPageNum, targetContent\)/,
  "deck rollback must restore by page number and recreate missing pages instead of relying on stale slide ids"
);
assert.match(
  source,
  /isWorkflowTransitionMessage\(msg\)[\s\S]*msg\.role === "system"[\s\S]*!msg\.gate[\s\S]*!isMessageFromCurrentGate\(msg\)/,
  "workflow transition notes must not reappear as current guidance after reload"
);
assert.match(
  source,
  /function isQualityReportChatMessage[\s\S]*quality-report-[\s\S]*还不能交付最终稿[\s\S]*可以交付最终稿[\s\S]*updateProjectChatMessages\(projectId, "visual"[\s\S]*filter\(\(m\) => !isQualityReportChatMessage\(m\)\)/,
  "new quality reports must replace stale quality reports so the Agent panel does not show contradictory delivery guidance"
);
assert.match(
  source,
  /function replaceMarkdownOpeningTag[\s\S]*new RegExp\(`<\$\{tag\}\\\\b\[\^>\]\*>`[\s\S]*replaceMarkdownOpeningTag\(html, "strong"/,
  "chat markdown styling must replace the whole opening tag so styled Markdown does not render stray > characters"
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
  /const isRequestExecutionAllowed = \(\) => \{[\s\S]*selectedProjectIdRef\.current !== requestProjectId[\s\S]*return false[\s\S]*latestGate\.gate === requestGate[\s\S]*latestGate\.gateRevision === requestGateRevision/,
  "Agent request execution must stop after switching projects so old streams cannot mutate the active workspace"
);
assert.match(
  source,
  /const appendRequestMessage = \([\s\S]*!isRequestExecutionAllowed\(\)[\s\S]*withRequestGateMeta\(message\)[\s\S]*appendProjectChatMessage\(requestProjectId, requestAgentRole, normalized\)/,
  "request-scoped Agent replies must persist to the original project even when it is no longer visible"
);
assert.match(
  source,
  /const frontendWillHandleAgentReply = isRequestExecutionAllowed\(\) && \(/,
  "frontend-handled Agent actions must only execute while the request is still current"
);
assert.match(
  source,
  /const refreshRequestSlides = async \(\) => \{[\s\S]*const freshSlides = await fetchSlides\(requestProjectId\)[\s\S]*slidesCacheRef\.current\[requestProjectId\] = freshSlides[\s\S]*selectedProjectIdRef\.current === requestProjectId/,
  "request-scoped Agent actions must refresh the original project without requiring it to be visible"
);
const syncRequestVisualPromptsStart = source.indexOf("const syncRequestVisualPrompts = async");
const syncRequestVisualPromptsEnd = source.indexOf("const hasAttachments =", syncRequestVisualPromptsStart);
assert.ok(
  syncRequestVisualPromptsStart >= 0 && syncRequestVisualPromptsEnd > syncRequestVisualPromptsStart,
  "must find request-scoped visual prompt sync helper"
);
const syncRequestVisualPromptsSource = source.slice(syncRequestVisualPromptsStart, syncRequestVisualPromptsEnd);
assert.match(
  syncRequestVisualPromptsSource,
  /if \(staleOverride\.visual && !staleOverride\.content\) \{[\s\S]*await generatePrompts\(requestProjectId, pageNums, buildCrossStageContext\("visual"\)\)[\s\S]*return freshSlides;/,
  "manual visual-description edits must only regenerate prompts, not overwrite the edited visual plan"
);
const multiPageVisualUpdateStart = source.indexOf('if (result.action === "update_all_slides_visual"');
const multiPageVisualUpdateEnd = source.indexOf("// Agent 理解用户想生图", multiPageVisualUpdateStart);
assert.ok(
  multiPageVisualUpdateStart >= 0 && multiPageVisualUpdateEnd > multiPageVisualUpdateStart,
  "must find multi-page visual update handler"
);
const multiPageVisualUpdateSource = source.slice(multiPageVisualUpdateStart, multiPageVisualUpdateEnd);
assert.doesNotMatch(
  multiPageVisualUpdateSource,
  /setActiveChatMessages/,
  "multi-page visual update execution status must persist in project-scoped chat when the user switches away"
);
assert.match(
  multiPageVisualUpdateSource,
  /const allowedPageNums = requestContext\.scope === "selected_slides"[\s\S]*requestContext\.pageNums[\s\S]*allowedPageNums\.has\(pageNum\)/,
  "selected-scope multi-page visual updates must stay limited to the pages captured at submit time"
);
assert.doesNotMatch(
  multiPageVisualUpdateSource,
  /selectedProject\.id/,
  "multi-page visual updates must write to the request project, not the currently visible project"
);
assert.match(
  multiPageVisualUpdateSource,
  /await updateVisualPlan\(requestProjectId, pageNum, patch\.visual_json, slide\.id\)[\s\S]*await refreshRequestSlides\(\)/,
  "multi-page visual updates must persist and refresh through the request-scoped project snapshot"
);
const multiPageContentUpdateStart = source.indexOf('if (result.action === "update_all_slides"');
const multiPageContentUpdateEnd = source.indexOf("// Agent 要求在当前页前面插入新页", multiPageContentUpdateStart);
assert.ok(
  multiPageContentUpdateStart >= 0 && multiPageContentUpdateEnd > multiPageContentUpdateStart,
  "must find multi-page content update handler"
);
const multiPageContentUpdateSource = source.slice(multiPageContentUpdateStart, multiPageContentUpdateEnd);
assert.doesNotMatch(
  multiPageContentUpdateSource,
  /selectedProject\.id/,
  "multi-page content updates must write to the request project, not the currently visible project"
);
assert.match(
  multiPageContentUpdateSource,
  /const allowedPageNums = requestContext\.scope === "selected_slides"[\s\S]*requestContext\.pageNums[\s\S]*allowedPageNums\.has\(pageNum\)/,
  "selected-scope multi-page content updates must stay limited to the pages captured at submit time"
);
assert.match(
  multiPageContentUpdateSource,
  /await updateSlideContent\(requestProjectId, pageNum, slidePatch\)[\s\S]*await refreshRequestSlides\(\)/,
  "multi-page content updates must persist and refresh through the request-scoped project snapshot"
);
assert.match(
  source,
  /const startContentPlanPoll = async \([\s\S]*Promise<GateActionResult>[\s\S]*正在读取原 PPT 的文字和页面截图，准备生成内容规划。[\s\S]*return \{ ok: true, runId: result\?\.run\?\.id \};/,
  "content-plan generation must show immediate status and report startup success"
);
assert.doesNotMatch(
  source,
  /OCR pipeline|Intent Contract|classification=/,
  "user-facing workflow copy must not expose internal processing labels"
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
  /const deferContentPlanPoll = \(message: string\)[\s\S]*内容规划仍在后台生成：/,
  "content-plan polling timeouts must not be presented as generation failures when the backend may still finish"
);
assert.match(
  source,
  /pg-slide-body-preview markdown-body[\s\S]*renderMarkdown\(text\.body\)/,
  "slide-card body previews must preserve generated line breaks instead of collapsing agenda items"
);
assert.match(
  css,
  /\.pg-slide-body-preview p[\s\S]*white-space: pre-wrap/,
  "slide-card Markdown paragraphs must preserve line breaks from content planning"
);
assert.match(
  css,
  /\.pg-slide-grid > \.pg-slide-card\s*\{[^}]*flex-grow:\s*0\s*!important[^}]*flex-shrink:\s*0\s*!important[^}]*\}/,
  "slide cards must not stretch to fill an incomplete final row"
);
assert.doesNotMatch(
  source,
  /failContentPlanPoll\("前端等待超时/,
  "content-plan frontend wait timeouts must not use the failure path"
);
assert.match(
  source,
  /if \(currentSlides\.length > 0 && currentSlideIds !== previousSlideIds && !activeContentPlan && contentPlanSucceeded\) \{[\s\S]*options\?\.onStarted\?\.\(\);[\s\S]*setContentPlanSnapshot/,
  "Brief Studio draft should only be cleared after new content-plan slides exist and the backend run has succeeded"
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
  /const isVisualRelevantStageContext = \([\s\S]*if \(role === "user"\) return true;/,
  "cross-stage context must treat user inputs as requirements without keyword filtering"
);
assert.match(
  source,
  /function buildVisualStyleGenerationContext\([\s\S]*if \(role === "用户"\) \{[\s\S]*lines\.push\(`用户：\$\{content\}`\)/,
  "style proposal generation must preserve recent user inputs as action requirements instead of filtering by style keywords"
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
  /const DEFAULT_PROTOTYPE_SAMPLE_COUNT = 3;[\s\S]*const PROTOTYPE_FAMILY_ORDER = \["bookend", "toc", "content", "section"[\s\S]*const defaultPrototypePageNumsForSlides = \(slides: Slide\[]\): number\[] => \{[\s\S]*\.slice\(0, DEFAULT_PROTOTYPE_SAMPLE_COUNT\)[\s\S]*const getPrototypeTargetSlides = \(explicitPageNums: number\[] = \[]\)[\s\S]*prototypeSelectionTouched \? Array\.from\(selectedPages\) : defaultPrototypePageNums[\s\S]*const defaultPrototypePageNums = defaultPrototypePageNumsForSlides\(slides\);/,
  "default prototype generation must include a content page in the three-page sample"
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
  /currentProjectStatus\?\.last_run[\s\S]*evaluateImageGenerationOutcome\([\s\S]*上一轮打样未完成[\s\S]*重打样张/,
  "the Agent status panel must explain a cancelled or failed prototype run instead of silently returning to generic sampling guidance"
);
assert.match(
  source,
  /currentStageNudge\.primary[\s\S]*onClick=\{currentStageNudge\.primary\.onClick\}[\s\S]*\{currentStageNudge\.primary\.label\}/,
  "the Agent status panel must render its primary next action instead of burying it in state"
);
assert.match(
  workflow,
  /key: "start-prototype"[\s\S]*disabled: !canStartPrototypeGeneration \|\| prototypePromptTargetCount === 0/,
  "the sample status-card action must use prototype readiness, not full-deck readiness"
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
assert.doesNotMatch(
  source,
  /TemplateRecommender|showTemplateRecommender|case "templates"/,
  "template recommendations must not create a separate workflow branch"
);
assert.match(
  source,
  /hasTemplateSource[\s\S]*生成模板视觉方向[\s\S]*pg-template-source-strip/,
  "uploaded templates must be integrated into the style proposal decision surface"
);
assert.match(
  css,
  /pg-template-source-strip[\s\S]*pg-style-swatch-group/,
  "template source and palette swatches need explicit integrated UI treatment"
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
assert.match(
  source,
  /保存并重新生成/,
  "single-slide edit primary action must say it will regenerate, not just generate"
);
const slideImagePreviewStart = source.indexOf('alt={"Slide " + slide.page_num}');
const slideImagePreviewEnd = source.indexOf("onError={(e) => {", slideImagePreviewStart);
assert.ok(slideImagePreviewStart >= 0 && slideImagePreviewEnd > slideImagePreviewStart, "must find global slide image preview click handler");
const slideImagePreviewHandler = source.slice(slideImagePreviewStart, slideImagePreviewEnd);
assert.match(
  slideImagePreviewHandler,
  /e\.stopPropagation\(\);[\s\S]*setGalleryModal\(\{ urls: allUrls[\s\S]*title: "PPT 预览"/,
  "global slide image click must open the deck preview in place"
);
assert.doesNotMatch(
  slideImagePreviewHandler,
  /handleEnterEdit|activateFinetuneForSlide/,
  "global slide image preview must not enter single-page edit mode"
);
assert.match(
  source,
  /const handleRegenerateSlideFromEdits = async[\s\S]*generateVisualPrompts\(projectId, pageNums, stageContext\)[\s\S]*pollUntilStatusNotGenerating\(projectId\)[\s\S]*startGeneration\(projectId, pageNums\)/,
  "save-and-regenerate must refresh page visual description and prompt through the unified visual-prompts run before regenerating the page image"
);
assert.doesNotMatch(
  source.slice(source.indexOf("const handleRegenerateSlideFromEdits = async"), source.indexOf("// 更新画面方案：只更新画面描述/提示词", source.indexOf("const handleRegenerateSlideFromEdits = async"))),
  /await generateVisualPlan\(projectId|await generatePrompts\(projectId/,
  "save-and-regenerate must not use legacy synchronous visual-plan or prompt endpoints"
);
assert.match(
  source,
  /const handleRegenerateSlideFromEdits = async[\s\S]*updateSinglePageRunMessage\(`正在保存修改并重新生成第 \$\{slide\.page_num\} 页\.\.\.`\)[\s\S]*updateFinetuneChatMessages\(slideId[\s\S]*第 \$\{slide\.page_num\} 页已重新生成/,
  "save-and-regenerate must leave visible single-page progress and completion feedback"
);
assert.match(
  source,
  /视觉阶段的内容变动只影响相关页面[\s\S]*setStaleMap[\s\S]*content: true[\s\S]*setContentPlanSnapshot\(data\)/,
  "visual-stage content edits must mark affected pages stale instead of reopening content confirmation"
);
assert.match(
  source,
  /const hydrateSlideStaleMap = \(items: Slide\[\]\) => \{[\s\S]*if \(backendStale\.content\) hydrated\.content = true;[\s\S]*if \(backendStale\.visual\) hydrated\.visual = true;[\s\S]*if \(backendStale\.image \|\| prevStale\.localImage\) hydrated\.image = true;[\s\S]*delete next\[slide\.id\];/,
  "slide stale hydration must remove backend-cleared content/visual flags instead of preserving visual-prompt intermediate state"
);
assert.match(
  source,
  /className="pg-agent-command-bar"/,
  "Agent composer guidance must be a compact command bar instead of a full task-card form"
);
assert.match(
  source,
  /currentAgentRole !== "finetune"\s*&&\s*\([\s\S]{0,500}<div className="pg-agent-command-bar">/,
  "single-page finetune must not show a duplicate command status bar once the target card explains the page and scope"
);
assert.match(
  source,
  /if \(currentProjectStatus\?\.active_run\) \{\s*updateProjectChatMessages\(projectId,\s*"visual",\s*\(prevMsgs\) => \{[\s\S]*return prevMsgs\.filter\(\(m\) => !isQualityReportChatMessage\(m\)\);[\s\S]*\}\);\s*return;\s*\}/,
  "active workflow runs must remove stale quality-report next-step guidance from the Agent panel"
);
assert.match(
  source,
  /case "generate_content_plan": \{[\s\S]*const chatContext = buildVisualStyleGenerationContext\([\s\S]*await dispatchGateAction\("generate_content_plan", \{[\s\S]*chat_context: chatContext,/,
  "Agent next-action content-plan starts must pass chat context so summarized topics do not lose the original page-count request"
);
const updateStaleStart = source.indexOf("const handleUpdateStaleSlides = async");
const updateStaleEnd = source.indexOf("// 用户确认后，重新生成 image 标记的页面。", updateStaleStart);
assert.ok(updateStaleStart >= 0 && updateStaleEnd > updateStaleStart, "must find stale visual update handler");
const updateStaleSource = source.slice(updateStaleStart, updateStaleEnd);
assert.match(
  updateStaleSource,
  /generateVisualPrompts\(projectId,\s*pageNumsForPrompt,\s*buildCrossStageContext\("visual"\)\)/,
  "stale visual updates must use the unified visual-prompts run so the main progress card can show page-by-page progress"
);
assert.match(
  updateStaleSource,
  /handleWorkflowRunStarted\(projectId,\s*startResult\.run\)/,
  "stale visual updates must adopt the returned visual-prompts run immediately instead of waiting for a manual refresh"
);
assert.doesNotMatch(
  updateStaleSource,
  /await generateVisualPlan\(projectId/,
  "stale visual updates must not use the legacy synchronous visual-plan endpoint without active_run progress"
);
assert.match(
  source,
  /const handleWorkflowRunStarted = useCallback\([\s\S]*adoptWorkflowRun[\s\S]*void refreshWorkflowStatus\(\)/,
  "run-starting actions must synchronously adopt the returned run and then refresh workflow status"
);
assert.match(
  source,
  /const handleStartGeneration = async[\s\S]*const result = await startGeneration\(projectId, pageNums, prototype\);[\s\S]*handleWorkflowRunStarted\(projectId,\s*result\.run\)/,
  "image generation starts must enter active workflow state from the returned run"
);
assert.match(
  source,
  /const handleGeneratePrompts = async[\s\S]*const startResult = await generateVisualPrompts\(projectId, pageNums, buildCrossStageContext\("visual"\)\);[\s\S]*handleWorkflowRunStarted\(projectId,\s*startResult\.run\)/,
  "visual prompt generation starts must enter active workflow state from the returned run"
);
assert.doesNotMatch(
  source,
  /className="pg-agent-command-sheet"[\s\S]{0,2600}<span>任务<\/span>[\s\S]{0,2600}<span>结果<\/span>/,
  "Agent composer guidance must not expose internal task/result field rows in the narrow sidebar"
);
assert.match(
  css,
  /\.pg-agent-command-bar[\s\S]*\.pg-agent-scope-panel/,
  "compact Agent command bar and its scope panel must have explicit styling"
);
assert.doesNotMatch(
  source,
  /pg-agent-context-capsule[\s\S]*当前理解/,
  "Agent sidebar must not duplicate the interactive command bar with a separate current-understanding card"
);
assert.match(
  source,
  /selectedProject\?\.intent_contract[\s\S]*pg-agent-contract-summary[\s\S]*项目背景/,
  "Agent sidebar must label the folded brief-derived summary in user language"
);
assert.doesNotMatch(
  source,
  /项目契约/,
  "Agent sidebar copy must not expose the internal project contract term"
);
assert.doesNotMatch(
  source,
  /pickContractValue\(contract, \["page_count", "target_page_count", "estimated_pages"\]\) \|\| \(slideCount/,
  "Agent brief summary must not appear only because the deck has a slide count"
);
assert.match(
  source,
  /quality_report[\s\S]*pg-agent-delivery-check[\s\S]*交付检查/,
  "Agent sidebar must show deterministic delivery-check hints without AI scoring"
);
assert.match(
  source,
  /pg-agent-task-card[\s\S]*handleAgentNextAction/,
  "Agent next actions must render as task cards instead of plain chat buttons"
);
assert.match(
  source,
  /pg-agent-execution-event[\s\S]*msg\.runId/,
  "Agent execution events must be visually separated from normal conversation"
);
assert.match(
  source,
  /pg-agent-material-group[\s\S]*本轮材料[\s\S]*pg-agent-material-group[\s\S]*项目资产/,
  "Agent material entry must separate per-message materials from project assets"
);
assert.match(
  css,
  /\.pg-agent-contract-summary[\s\S]*\.pg-agent-material-group[\s\S]*\.pg-agent-task-card/,
  "Agent sidebar additions must have dedicated responsive styles"
);
assert.match(
  css,
  /\.pg-agent-area-panel button\.is-active b[\s\S]*color:\s*#fff(?:fff)?[\s\S]*\.pg-agent-area-panel button\.is-active span[\s\S]*color:\s*#fff(?:fff)?/,
  "Agent area picker active option must keep both label and hint readable on the dark selected background"
);
assert.match(
  source,
  /className="pg-agent-command-sentence"[\s\S]*<span>将修改<\/span>[\s\S]*\{agentScopeButtonLabel\}[\s\S]*<span>的<\/span>[\s\S]*\{agentAreaButtonLabel\}/,
  "Agent command bar must read as a fill-in sentence: 将修改 [范围] 的 [区域]"
);
assert.doesNotMatch(
  source,
  /pg-agent-tabs[\s\S]{0,2400}内容总监[\s\S]{0,2400}视觉总监[\s\S]{0,2400}单页微调/,
  "Agent sidebar must not expose separate role tabs; routing should stay internal to the unified Agent surface"
);
assert.match(
  source,
  /:\s*"整套 PPT";/,
  "Agent scope chip must use a stable deck label instead of repeating the slide count"
);
assert.match(
  source,
  /composerRequestContext\.targetArea === "whole" \? "全页内容" : composerRequestContext\.areaLabel/,
  "Agent area chip must clarify that whole means the page content area, not another page scope"
);
assert.match(
  source,
  /className=\{`pg-composer-attach-button[\s\S]*title="添加参考材料"/,
  "Agent reference upload must live beside the chat input instead of in the scope command bar"
);
assert.match(
  css,
  /\.pg-agent-command-chip\s*\{[\s\S]*white-space:\s*nowrap;[\s\S]*\}/,
  "Agent command chip labels must not wrap within a pill"
);
assert.doesNotMatch(
  source,
  /pg-agent-command-summary[\s\S]{0,2200}title="添加参考材料"/,
  "Agent reference upload must not sit at the same hierarchy as scope and area chips"
);
assert.match(
  source,
  /target_area:\s*requestContext\.targetArea[\s\S]*area_label:\s*requestContext\.areaLabel[\s\S]*confidence:\s*requestContext\.confidence/,
  "Agent page_context must carry the inferred page area so the backend can act on the same target the UI shows"
);
assert.match(
  source,
  /把选中页改得更商务，背景更克制/,
  "Agent composer visual placeholder must use a concrete example"
);
assert.match(
  source,
  /把第 3 页标题改短，正文更像汇报口吻/,
  "Agent composer content placeholder must use a concrete example"
);
assert.match(
  source,
  /保留文字，把画面换成更高级的办公室场景/,
  "Agent composer placeholder must use concrete examples for visual, content, and single-page fine-tune states"
);
assert.doesNotMatch(
  source,
  /currentAgentRole === "finetune"[\s\S]{0,700}:\s*"输入指令\.\.\."/,
  "Agent composer must not fall back to a generic input prompt in the main post-draft states"
);
assert.match(
  source,
  /先在画布中勾选页面，或改说具体页码/,
  "Agent composer must block ambiguous 'these pages' commands when no pages are selected"
);
assert.match(
  source,
  /requestContext\.risk !== "safe"[\s\S]*会影响整套[\s\S]*会产生生图成本[\s\S]*会覆盖现有画面方案/,
  "Agent composer must confirm cost/destructive requests with user-outcome consequences before sending"
);

const allowedDirectSetContexts = [
  "const setActiveChatMessages",
  "const updateRoleChatMessages",
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
assert.match(
  workflowHook,
  /const scopedWorkflowStatus = workflowStatus\?\.project_id === projectId \? workflowStatus : null;[\s\S]*const activeRun = scopedWorkflowStatus\?\.active_run \|\| null;/,
  "workflow active run state must be scoped to the active project id"
);

assert.match(
  source,
  /visual_directive_suggestions[\s\S]*移到画面要求/,
  "slide editor must surface visual directive suggestions from content saves"
);
assert.doesNotMatch(
  source,
  /title="插入(?:表格|飞轮|流程图|对比矩阵)"/,
  "body editor toolbar must not expose structure-diagram insertion controls"
);
assert.doesNotMatch(
  source,
  /pg-insert-menu-title">结构/,
  "slash menu must keep structure diagrams out of the body editor"
);

console.log("project isolation tests passed");
