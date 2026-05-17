import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_URL = process.env.APP_URL || "http://127.0.0.1:4174";
const API_BASE = process.env.VITE_API_BASE_URL || APP_URL;
const APP_ORIGIN = new URL(APP_URL).origin;
const API_ORIGIN = new URL(API_BASE, APP_URL).origin;
const testerAuth = {
  testerId: "22222222-2222-4222-8222-222222222222",
  displayName: "Brief Submit Tester",
};

const project = {
  id: "brief-submit-project",
  title: "Brief Submit E2E",
  status: "draft",
  style_id: null,
  style_proposal: null,
  selected_style: null,
  selected_template_recommendations: null,
  created_at: new Date().toISOString(),
  completed_slides: 0,
  content_plan_confirmed: false,
};

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
    [viteJs, "preview", "--host", previewUrl.hostname, "--port", previewUrl.port || "4174", "--strictPort"],
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

function workflowStatus(active = false) {
  const activeRun = active
    ? {
        id: "brief-run",
        kind: "content_plan",
        status: "running",
        stage: "content_plan",
        message: "正在生成第 1/80 页...",
        total_count: 80,
        completed_count: 1,
      }
    : null;
  return {
    project_id: project.id,
    project_phase: project.status,
    project_status: project.status,
    total_slides: 0,
    completed_slides: 0,
    target_count: active ? 80 : 0,
    active_run: activeRun,
    last_run: activeRun,
    progress: active
      ? { run_id: "brief-run", kind: "content_plan", status: "running", current: 1, total: 80, total_pages: 80 }
      : null,
    has_pptx: false,
    slides: [],
  };
}

let previewChild = null;
if (!(await isAppReachable(APP_URL))) {
  previewChild = await startPreviewServer();
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const contentPlanBodies = [];
const uploadedDocuments = [];

await page.addInitScript((projectId) => {
  window.localStorage.setItem("ppt_god_last_project_id", projectId);
  window.localStorage.setItem(
    "pptgod.mvpAuth",
    JSON.stringify({
      testerId: "22222222-2222-4222-8222-222222222222",
      displayName: "Brief Submit Tester",
    }),
  );
}, project.id);

await page.route("**/*", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const pathName = url.pathname;
  const method = request.method();
  const mockableApiOrigin = url.origin === APP_ORIGIN || url.origin === API_ORIGIN;
  if (!mockableApiOrigin) return route.continue();

  if (method === "GET" && pathName === "/auth/me") {
    return route.fulfill({ json: { tester_id: testerAuth.testerId, display_name: testerAuth.displayName } });
  }
  if (method === "GET" && pathName === "/projects") return route.fulfill({ json: [project] });
  if (method === "GET" && pathName === `/projects/${project.id}`) return route.fulfill({ json: project });
  if (method === "GET" && pathName === `/projects/${project.id}/slides`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/reference-images`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/documents`) return route.fulfill({ json: uploadedDocuments });
  if (method === "GET" && pathName === `/projects/${project.id}/template-pages`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/generation-progress`) {
    return route.fulfill({ json: { project_id: project.id, project_status: project.status } });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/workflow-status`) {
    return route.fulfill({ json: workflowStatus(contentPlanBodies.length > 0) });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/status`) {
    return route.fulfill({ json: workflowStatus(contentPlanBodies.length > 0) });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/content-plan`) {
    contentPlanBodies.push(request.postDataJSON());
    await new Promise((resolve) => setTimeout(resolve, 300));
    return route.fulfill({
      json: {
        message: "Content plan generation started",
        status: "draft",
        run: workflowStatus(true).active_run,
      },
    });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/upload-document`) {
    const doc = {
      filename: "deck.md",
      char_count: 0,
      text_parse_status: "queued",
      text_preview: "",
      asset_extraction_status: "not_applicable",
      extracted_assets: {},
    };
    uploadedDocuments.splice(0, uploadedDocuments.length, doc);
    return route.fulfill({ json: doc });
  }

  if (pathName.startsWith("/projects") || pathName.startsWith("/auth")) {
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock route: ${method} ${pathName}` } });
  }
  return route.continue();
});

try {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByText("Brief Submit E2E").first().waitFor({ timeout: 10_000 });

  const brief =
    "把这个 MD 文件做成 60 到 80 页的 PPT。用户群体是大连混沌的学员，目标是上课，演讲时长 1.5 小时。";
  await page.locator(".pg-brief-editor").first().fill(brief);
  const dataTransfer = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["# Deck\n\nSource notes."], "deck.md", { type: "text/markdown" }));
    return transfer;
  });
  await page.locator(".pg-brief-studio").first().dispatchEvent("drop", { dataTransfer });
  await page.locator(".pg-brief-inline-chip").filter({ hasText: "deck.md" }).waitFor({ timeout: 10_000 });
  const submitButton = page.getByRole("button", { name: "生成内容规划" }).first();
  await submitButton.click({ clickCount: 3, delay: 0 });

  for (let i = 0; i < 50 && contentPlanBodies.length === 0; i += 1) {
    await page.waitForTimeout(100);
  }

  assert.equal(contentPlanBodies.length, 1, "rapid clicks should create exactly one content-plan request");
  assert.equal(contentPlanBodies[0].page_count, 80);
  assert.match(contentPlanBodies[0].topic, /60 到 80 页/);
  assert.match(contentPlanBodies[0].topic, /【文件：deck\.md】/);
  assert.match(contentPlanBodies[0].topic, /大连混沌/);
  assert.match(contentPlanBodies[0].topic, /1\.5 小时/);
  console.log("Brief submit E2E passed.");
} finally {
  await browser.close();
  if (previewChild) previewChild.kill("SIGTERM");
}
