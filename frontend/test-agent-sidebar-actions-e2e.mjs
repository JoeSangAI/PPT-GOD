import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_URL = process.env.APP_URL || "http://127.0.0.1:4176";
const API_BASE = process.env.VITE_API_BASE_URL || APP_URL;
const APP_ORIGIN = new URL(APP_URL).origin;
const API_ORIGIN = new URL(API_BASE, APP_URL).origin;
const testerAuth = {
  testerId: "44444444-4444-4444-8444-444444444444",
  displayName: "Agent Sidebar Tester",
};
const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
  "base64",
);

async function isAppReachable(url) {
  try {
    await fetch(url, { method: "GET", signal: AbortSignal.timeout(3000) });
    return true;
  } catch {
    return false;
  }
}

async function startPreviewServer() {
  const viteJs = path.join(__dirname, "node_modules", "vite", "bin", "vite.js");
  const previewUrl = new URL(APP_URL);
  const proc = spawn(
    process.execPath,
    [viteJs, "preview", "--host", previewUrl.hostname, "--port", previewUrl.port || "4176", "--strictPort"],
    { cwd: __dirname, stdio: "ignore" },
  );
  for (let i = 0; i < 60; i += 1) {
    if (await isAppReachable(APP_URL)) return proc;
    if (proc.exitCode !== null) throw new Error(`vite preview exited before ${APP_URL} was ready.`);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  proc.kill("SIGTERM");
  throw new Error(`Timed out waiting for preview server at ${APP_URL}`);
}

const project = {
  id: "agent-sidebar-project",
  title: "Agent Sidebar E2E",
  status: "prompt_ready",
  style_id: null,
  style_proposal: null,
  selected_style: { name: "Mock Style" },
  selected_template_recommendations: null,
  intent_contract: {
    audience: "管理层",
    goal: "汇报增长策略",
    tone: "克制、清晰",
    page_count: "2 页",
  },
  created_at: new Date().toISOString(),
  completed_slides: 0,
  content_plan_confirmed: true,
};

const slides = [
  {
    id: "slide-1",
    page_num: 1,
    type: "cover",
    status: "prompt_ready",
    content_json: {
      page_num: 1,
      type: "cover",
      text_content: { headline: "增长策略", subhead: "Agent 栏测试", body: "" },
      content_blocks: [],
    },
    visual_json: { page_num: 1, visual_description: "浅色商务封面" },
    prompt_text: "Mock prompt 1",
    image_path: null,
    reference_images: [],
  },
  {
    id: "slide-2",
    page_num: 2,
    type: "content",
    status: "prompt_ready",
    content_json: {
      page_num: 2,
      type: "content",
      text_content: { headline: "增长闭环", subhead: "", body: "获客、激活、留存、推荐" },
      content_blocks: [],
    },
    visual_json: { page_num: 2, visual_description: "四象限结构" },
    prompt_text: "Mock prompt 2",
    image_path: null,
    reference_images: [],
  },
];

const state = {
  projects: [project],
  activeRun: null,
  lastRun: null,
  qualityReport: null,
  nextChatResult: null,
  chatRequests: [],
  generateCalls: [],
  stopCalls: 0,
  finetuneCalls: [],
};

function completedSlides() {
  return slides.filter((slide) => slide.status === "completed" && slide.image_path).length;
}

function workflowStatus() {
  const activeRun = state.activeRun;
  return {
    project_id: project.id,
    project_phase: project.status,
    project_status: project.status,
    total_slides: slides.length,
    completed_slides: completedSlides(),
    target_count: activeRun?.total_count || slides.length,
    target_page_nums: activeRun?.target_page_nums || null,
    active_run: activeRun,
    last_run: state.lastRun || activeRun,
    progress: activeRun
      ? {
          run_id: activeRun.id,
          kind: activeRun.kind,
          status: activeRun.status,
          current: activeRun.completed_count || 0,
          total: activeRun.total_count || slides.length,
          active_page_nums: activeRun.target_page_nums || [],
        }
      : null,
    quality_report: state.qualityReport,
    has_pptx: false,
    slides: slides.map((slide) => ({
      id: slide.id,
      page_num: slide.page_num,
      status: slide.status,
      error_msg: slide.error_msg || null,
      stale_flags: slide.stale_flags || {},
    })),
  };
}

function setReadyProject() {
  state.projects = [project];
  state.activeRun = null;
  state.lastRun = null;
  state.qualityReport = null;
  project.status = "prompt_ready";
  project.content_plan_confirmed = true;
  project.selected_style = { name: "Mock Style" };
  project.completed_slides = 0;
  for (const slide of slides) {
    slide.status = "prompt_ready";
    slide.image_path = null;
    slide.error_msg = null;
    slide.stale_flags = {};
  }
}

function makeRun({ kind = "prototype_generation", pageNums = [1], total = pageNums.length || slides.length } = {}) {
  return {
    id: `run-${state.generateCalls.length + 1}`,
    project_id: project.id,
    kind,
    status: "running",
    stage: kind,
    message: kind === "prototype_generation" ? "正在生成样张" : "正在生成图片",
    total_count: total,
    completed_count: 0,
    failed_count: 0,
    target_page_nums: pageNums,
  };
}

let previewChild = null;
if (!(await isAppReachable(APP_URL))) {
  previewChild = await startPreviewServer();
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

await page.addInitScript(({ projectId, auth }) => {
  window.sessionStorage.setItem("pptgod.mvpAuth", JSON.stringify(auth));
  window.localStorage.setItem("ppt_god_last_project_id", projectId);
}, { projectId: project.id, auth: testerAuth });

await page.route("**/*", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const method = request.method();
  const pathName = url.pathname;
  const mockableApiOrigin = url.origin === APP_ORIGIN || url.origin === API_ORIGIN;
  if (!mockableApiOrigin) return route.continue();

  if (pathName.startsWith("/outputs/")) {
    return route.fulfill({ status: 200, contentType: "image/png", body: onePixelPng });
  }
  if (method === "GET" && pathName === "/auth/me") {
    return route.fulfill({ json: { tester_id: testerAuth.testerId, display_name: testerAuth.displayName } });
  }
  if (method === "GET" && pathName === "/projects") return route.fulfill({ json: state.projects });
  if (method === "GET" && pathName === `/projects/${project.id}`) return route.fulfill({ json: project });
  if (method === "GET" && pathName === `/projects/${project.id}/slides`) return route.fulfill({ json: slides });
  if (method === "GET" && pathName === `/projects/${project.id}/reference-images`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/documents`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/template-pages`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/generation-progress`) {
    return route.fulfill({ json: { project_id: project.id, project_status: project.status, active_run: state.activeRun } });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/generation-status`) {
    return route.fulfill({
      json: { status: state.activeRun ? "running" : "idle", project_status: project.status, active_run: state.activeRun },
    });
  }
  if (method === "GET" && (pathName === `/projects/${project.id}/workflow-status` || pathName === `/projects/${project.id}/status`)) {
    return route.fulfill({ json: workflowStatus() });
  }
  if (method === "GET" && pathName.match(new RegExp(`^/projects/${project.id}/slides/[^/]+/versions$`))) {
    return route.fulfill({ json: [] });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/chat`) {
    const body = request.postDataJSON();
    state.chatRequests.push(body);
    const result = state.nextChatResult || { action: "answer", response: "已记录这次要求。" };
    state.nextChatResult = null;
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: `data: ${JSON.stringify({ type: "result", data: result })}\n\n`,
    });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/generate`) {
    const body = request.postDataJSON();
    state.generateCalls.push(body);
    const pageNums = body.page_nums?.length ? body.page_nums.map(Number) : slides.map((slide) => slide.page_num);
    const run = makeRun({
      kind: body.prototype ? "prototype_generation" : pageNums.length === 1 ? "page_generation" : "batch_generation",
      pageNums,
      total: pageNums.length,
    });
    state.activeRun = run;
    state.lastRun = run;
    project.status = "generating";
    for (const slide of slides) {
      if (pageNums.includes(slide.page_num)) slide.status = "generating";
    }
    return route.fulfill({ json: { message: "Generation started", project_id: project.id, run, task_id: run.id } });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/stop-generation`) {
    state.stopCalls += 1;
    state.lastRun = state.activeRun ? { ...state.activeRun, status: "cancelled", message: "已停止生成" } : null;
    state.activeRun = null;
    project.status = "prompt_ready";
    for (const slide of slides) {
      if (slide.status === "generating") slide.status = "prompt_ready";
    }
    return route.fulfill({ json: { message: "已停止生成", status: "prompt_ready" } });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/slides/slide-1/finetune`) {
    const body = request.postDataJSON();
    state.finetuneCalls.push(body);
    project.status = "completed";
    slides[0].status = "completed";
    slides[0].image_path = "/outputs/agent-sidebar-project/slide_01_finetune.png";
    return route.fulfill({ json: { message: "Finetune completed" } });
  }

  if (pathName.startsWith("/projects") || pathName.startsWith("/auth")) {
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock route: ${method} ${pathName}` } });
  }
  return route.continue();
});

try {
  state.projects = [];
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByText("请先新建或选择一个项目").waitFor({ timeout: 10_000 });
  assert.equal(await page.locator(".pg-agent-tabs").count(), 0, "empty workspace must not show role tabs");
  assert.equal(await page.locator(".pg-agent-tab").count(), 0, "empty workspace must not show role buttons");

  setReadyProject();
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText("Agent Sidebar E2E").first().waitFor({ timeout: 10_000 });
  assert.equal(await page.locator(".pg-agent-context-capsule").count(), 0, "Agent sidebar should not duplicate the command bar");
  await page.getByText("项目背景").waitFor({ timeout: 10_000 });
  await page.getByText("将修改", { exact: true }).waitFor({ timeout: 10_000 });
  await page.getByText("整套 PPT").first().waitFor({ timeout: 10_000 });
  assert.equal(await page.locator(".pg-agent-tabs").count(), 0, "ready project must not show role tabs");

  await page.getByLabel("添加参考材料").click();
  await page.getByText("本轮材料").waitFor({ timeout: 10_000 });
  await page.getByText("项目资产").waitFor({ timeout: 10_000 });
  await page.getByLabel("添加参考材料").click();
  await page.getByTitle("调整本次修改范围").click();
  await page.getByText("当前页").waitFor({ timeout: 10_000 });
  await page.getByText("选中页").waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^整套\s+2 页$/ }).waitFor({ timeout: 10_000 });
  await page.getByTitle("调整本次修改区域").click();
  await page.getByRole("button", { name: /^标题\s+标题、副标题$/ }).waitFor({ timeout: 10_000 });
  const activeAreaButton = page.getByRole("button", { name: /^画面\s+背景、风格、版式$/ });
  await activeAreaButton.waitFor({ timeout: 10_000 });
  const activeAreaTextColors = await activeAreaButton.evaluate((button) =>
    Array.from(button.querySelectorAll("b, span")).map((item) => window.getComputedStyle(item).color)
  );
  assert.ok(
    activeAreaTextColors.every((color) => color === "rgb(255, 255, 255)"),
    `active area option text should stay readable on dark background, got ${activeAreaTextColors.join(", ")}`
  );

  await page.locator("textarea.pg-chat-input").fill("第 1 页背景更有科技感");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.getByText("已记录这次要求。").waitFor({ timeout: 10_000 });
  assert.equal(state.chatRequests.at(-1).agent_role, "visual", "visual request should route to visual agent internally");
  assert.deepEqual(state.chatRequests.at(-1).page_context.target_page_nums, [1]);
  assert.equal(state.chatRequests.at(-1).page_context.target_area, "visual");

  state.nextChatResult = {
    action: "answer",
    response: "可以生成第 1 页图片。",
    next_action: {
      type: "generate_images",
      label: "生成第 1 页图片",
      description: "按当前画面方案生成这一页。",
      payload: { page_nums: [1] },
    },
  };
  await page.locator("textarea.pg-chat-input").fill("下一步是什么");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.locator(".pg-agent-task-card").waitFor({ timeout: 10_000 });
  await page.locator(".pg-agent-task-card").getByRole("button", { name: "生成第 1 页图片" }).click();
  await page.getByText("停止生成").waitFor({ timeout: 10_000 });
  assert.equal(state.generateCalls.length, 1, "Agent task-card action should start generation exactly once");
  assert.equal(Boolean(state.generateCalls[0].prototype), false);
  assert.deepEqual(state.generateCalls[0].page_nums, [1]);

  await page.getByRole("button", { name: "停止生成" }).click();
  await page.getByText("已停止生成", { exact: true }).waitFor({ timeout: 10_000 });
  assert.equal(state.stopCalls, 1, "Agent run control should stop active generation");

  setReadyProject();
  state.qualityReport = { message: "还有交付检查项需要复核。", issues: [{ page_num: 2, message: "视觉未更新" }] };
  slides[1].status = "failed";
  slides[1].error_msg = "Mock image failed";
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText("交付检查", { exact: true }).waitFor({ timeout: 10_000 });
  await page.getByText("还有交付检查项需要复核。").waitFor({ timeout: 10_000 });
  await page.locator(".pg-agent-delivery-check").getByText("第 2 页生成失败，可重试。").waitFor({ timeout: 10_000 });

  setReadyProject();
  project.status = "completed";
  project.completed_slides = 2;
  for (const slide of slides) {
    slide.status = "completed";
    slide.image_path = `/outputs/agent-sidebar-project/slide_${String(slide.page_num).padStart(2, "0")}.png`;
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".pg-slide-card").first().click();
  await page.getByText("微调工作台").waitFor({ timeout: 10_000 });
  await page.locator(".pg-finetune-target").waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: "✕" }).click();
  assert.equal(await page.locator(".pg-agent-tabs").count(), 0, "finetune mode must not reintroduce role tabs");
  await page.locator("textarea.pg-chat-input").fill("保留文字，把画面换成更高级的办公室场景");
  await page.getByRole("button", { name: "生成", exact: true }).click();
  await page.getByText(/已生成第 1 页的微调版本/).waitFor({ timeout: 10_000 });
  assert.equal(state.finetuneCalls.length, 1, "finetune should call the slide finetune endpoint once");
  assert.equal(state.finetuneCalls[0].instruction, "保留文字，把画面换成更高级的办公室场景");

  assert.deepEqual(pageErrors, [], "browser page should not throw runtime errors");
  console.log("Agent sidebar action E2E passed.");
} finally {
  await browser.close();
  if (previewChild) previewChild.kill("SIGTERM");
}
