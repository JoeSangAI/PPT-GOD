import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_URL = process.env.APP_URL || "http://127.0.0.1:4173";
const API_BASE = process.env.VITE_API_BASE_URL || "http://localhost:8000";
const APP_ORIGIN = new URL(APP_URL).origin;
const API_ORIGIN = new URL(API_BASE, APP_URL).origin;
const testerAuth = {
  testerId: "11111111-1111-4111-8111-111111111111",
  displayName: "Low Cost Tester",
};
const project = {
  id: "low-cost-project",
  title: "Low Cost E2E",
  status: "prompt_ready",
  style_id: null,
  style_proposal: null,
  selected_style: { name: "Mock Style" },
  selected_template_recommendations: null,
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
      text_content: { headline: "Mock cover", subhead: "", body: "" },
    },
    visual_json: {
      page_num: 1,
      visual_description: "Mock visual",
      is_seed_recommended: true,
      seed_family: "cover",
    },
    prompt_text: "Mock prompt",
    image_path: null,
    reference_images: [],
  },
];

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
    [viteJs, "preview", "--host", previewUrl.hostname, "--port", previewUrl.port || "8000", "--strictPort"],
    { cwd: __dirname, stdio: "ignore" },
  );
  for (let i = 0; i < 60; i++) {
    if (await isAppReachable(APP_URL)) return proc;
    if (proc.exitCode !== null) {
      throw new Error(
        `vite preview exited before ${APP_URL} was ready. Run "npm run build" so dist/ exists, or start the dev server manually.`,
      );
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  proc.kill("SIGTERM");
  throw new Error(`Timed out waiting for preview server at ${APP_URL}`);
}

let previewChild = null;
if (!(await isAppReachable(APP_URL))) {
  previewChild = await startPreviewServer();
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
let generateCalls = 0;
let stopCalls = 0;
let retryFailedCalls = 0;
let rollbackCalls = 0;
let chatCalls = 0;

function workflowStatus() {
  const activeRun = project.status === "generating"
    ? {
        id: "mock-task",
        kind: "prototype_generation",
        status: "running",
        stage: "image_generation",
        message: "Mock generation running",
      }
    : null;
  return {
    project_id: project.id,
    project_phase: project.status,
    project_status: project.status,
    total_slides: slides.length,
    completed_slides: slides.filter((s) => s.status === "completed").length,
    target_count: slides.length,
    target_page_nums: null,
    active_run: activeRun,
    last_run: activeRun,
    progress: activeRun
      ? { run_id: activeRun.id, kind: activeRun.kind, status: activeRun.status, current: 0, total: slides.length }
      : null,
    has_pptx: false,
    slides: slides.map((s) => ({ id: s.id, page_num: s.page_num, status: s.status, error_msg: null })),
  };
}

await page.addInitScript((projectId) => {
  window.localStorage.setItem("ppt_god_last_project_id", projectId);
  window.sessionStorage.setItem(
    "pptgod.mvpAuth",
    JSON.stringify({
      testerId: "11111111-1111-4111-8111-111111111111",
      displayName: "Low Cost Tester",
    }),
  );
}, project.id);

await page.route("**/*", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();
  const mockableApiOrigin = url.origin === APP_ORIGIN || url.origin === API_ORIGIN;
  if (!mockableApiOrigin) {
    return route.continue();
  }

  if (method === "GET" && path === "/auth/me") {
    return route.fulfill({ json: { tester_id: testerAuth.testerId, display_name: testerAuth.displayName } });
  }
  if (method === "GET" && path === "/projects") {
    return route.fulfill({ json: [project] });
  }
  if (method === "GET" && path === `/projects/${project.id}`) {
    return route.fulfill({ json: project });
  }
  if (method === "GET" && path === `/projects/${project.id}/slides`) {
    return route.fulfill({ json: slides });
  }
  if (method === "GET" && path === `/projects/${project.id}/reference-images`) {
    return route.fulfill({ json: [] });
  }
  if (method === "GET" && path === `/projects/${project.id}/documents`) {
    return route.fulfill({ json: [] });
  }
  if (method === "GET" && path === `/projects/${project.id}/template-pages`) {
    return route.fulfill({ json: [] });
  }
  if (method === "GET" && path === `/projects/${project.id}/generation-progress`) {
    return route.fulfill({ json: { project_id: project.id, project_status: project.status } });
  }
  if (method === "GET" && path === `/projects/${project.id}/workflow-status`) {
    return route.fulfill({ json: workflowStatus() });
  }
  if (method === "GET" && path === `/projects/${project.id}/generation-status`) {
    return route.fulfill({
      json: {
        status: project.status === "generating" ? "running" : "idle",
        project_status: project.status,
      },
    });
  }
  if (method === "GET" && path === `/projects/${project.id}/status`) {
    return route.fulfill({
      json: {
        project_id: project.id,
        project_status: project.status,
        total_slides: slides.length,
        completed_slides: slides.filter((s) => s.status === "completed").length,
        target_count: slides.length,
        target_page_nums: null,
        has_pptx: false,
        slides: slides.map((s) => ({
          page_num: s.page_num,
          status: s.status,
          error_msg: null,
        })),
      },
    });
  }
  if (method === "POST" && path === `/projects/${project.id}/generate`) {
    generateCalls++;
    const body = request.postDataJSON();
    assert.equal(body.prototype, true, "low-cost UI smoke should use prototype generation");
    project.status = "generating";
    slides[0].status = "generating";
    return route.fulfill({
      json: {
        message: "Generation started",
        project_id: project.id,
        prototype: true,
        page_nums: [1],
        task_id: "mock-task",
        run: workflowStatus().active_run,
      },
    });
  }
  if (method === "POST" && path === `/projects/${project.id}/stop-generation`) {
    stopCalls++;
    project.status = "prompt_ready";
    slides[0].status = "prompt_ready";
    return route.fulfill({ json: { message: "Generation stopped", status: "prompt_ready" } });
  }
  if (method === "POST" && path === `/projects/${project.id}/retry-failed`) {
    retryFailedCalls++;
    project.status = "generating";
    slides[0].status = "generating";
    return route.fulfill({ json: { message: "Retry started", page_nums: [1], count: 1 } });
  }
  if (method === "POST" && path === `/projects/${project.id}/rollback`) {
    rollbackCalls++;
    const body = request.postDataJSON();
    assert.equal(body.target_stage, "visual_ready");
    project.status = "visual_ready";
    project.selected_style = null;
    slides[0].status = "visual_ready";
    slides[0].prompt_text = null;
    return route.fulfill({ json: project });
  }
  if (method === "POST" && path === `/projects/${project.id}/chat`) {
    chatCalls++;
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: `data: ${JSON.stringify({ type: "error", message: "Mock chat failed" })}\n\n`,
    });
  }

  if (path.startsWith("/projects") || path.startsWith("/auth")) {
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock route: ${method} ${path}` } });
  }
  return route.continue();
});

try {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByText("Low Cost E2E").first().waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^生成样张\(\d+\s*页\)$/ }).first().click();
  await page.getByText("停止生成").waitFor({ timeout: 10_000 });
  await page.getByText("停止生成").click();
  await page.getByText("已停止生成", { exact: true }).waitFor({ timeout: 10_000 });

  project.status = "failed";
  slides[0].status = "failed";
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "重试失败页" }).first().click();
  await page.getByText("已启动 1 个失败页面的重试", { exact: true }).waitFor({ timeout: 10_000 });

  project.status = "prompt_ready";
  slides[0].status = "prompt_ready";
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /^2\s+视觉方案$/ }).click();
  await page.getByRole("button", { name: "确认" }).click();
  await page.getByText("回退成功", { exact: true }).waitFor({ timeout: 10_000 });

  project.status = "prototype_ready";
  project.selected_style = { name: "Mock Style" };
  slides[0].status = "completed";
  slides[0].image_path = "./outputs/low-cost-project/slide_01.png";
  slides[0].prompt_text = "Mock prompt";
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText(/^样张已生成/).waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: /^重打样张\(\d+\s*页\)$/ }).first().waitFor({ timeout: 10_000 });

  await page.locator("textarea.pg-chat-input").fill("测试聊天错误处理");
  await page.getByRole("button", { name: "发送" }).click();
  await page.getByText("❌ Mock chat failed").waitFor({ timeout: 10_000 });

  assert.equal(generateCalls, 1, "expected exactly one mocked generate call");
  assert.equal(stopCalls, 1, "expected exactly one mocked stop call");
  assert.equal(retryFailedCalls, 1, "expected exactly one mocked retry-failed call");
  assert.equal(rollbackCalls, 1, "expected exactly one mocked rollback call");
  assert.equal(chatCalls, 1, "expected exactly one mocked chat call");
  console.log("Low-cost E2E passed without real image generation.");
} finally {
  await browser.close();
  if (previewChild) {
    previewChild.kill("SIGTERM");
  }
}
