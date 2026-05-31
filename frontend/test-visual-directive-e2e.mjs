import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_URL = process.env.APP_URL || "http://127.0.0.1:4175";
const API_BASE = process.env.VITE_API_BASE_URL || APP_URL;
const APP_ORIGIN = new URL(APP_URL).origin;
const API_ORIGIN = new URL(API_BASE, APP_URL).origin;
const testerAuth = {
  testerId: "33333333-3333-4333-8333-333333333333",
  displayName: "Visual Directive Tester",
};
const initialBody = "增长的关键在于四个动作形成闭环。\n用增长飞轮表示：获客、激活、留存、推荐";

const project = {
  id: "visual-directive-project",
  title: "Visual Directive E2E",
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
    type: "content",
    status: "prompt_ready",
    content_json: {
      page_num: 1,
      type: "content",
      text_content: { headline: "增长闭环", subhead: "", body: initialBody },
      content_blocks: [{ id: "body", kind: "markdown", markdown: initialBody }],
      visual_suggestion: "",
      visual_requirements: [],
    },
    visual_json: { page_num: 1, visual_description: "Mock visual" },
    prompt_text: "Mock prompt",
    image_path: null,
    reference_images: [],
  },
];
const contentPatches = [];

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
    [viteJs, "preview", "--host", previewUrl.hostname, "--port", previewUrl.port || "4175", "--strictPort"],
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

function workflowStatus() {
  return {
    project_id: project.id,
    project_phase: project.status,
    project_status: project.status,
    total_slides: slides.length,
    completed_slides: 0,
    target_count: slides.length,
    target_page_nums: null,
    active_run: null,
    last_run: null,
    progress: null,
    has_pptx: false,
    slides: slides.map((slide) => ({ id: slide.id, page_num: slide.page_num, status: slide.status, error_msg: null })),
  };
}

function visualDirectiveSuggestions(body) {
  return String(body || "").includes("用增长飞轮表示")
    ? [{
        id: "vd_1",
        line_index: 0,
        original_text: "用增长飞轮表示：获客、激活、留存、推荐",
        directive: "用增长飞轮表示",
        kind: "flywheel",
        diagram_labels: ["获客", "激活", "留存", "推荐"],
      }]
    : [];
}

let previewChild = null;
if (!(await isAppReachable(APP_URL))) {
  previewChild = await startPreviewServer();
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

await page.addInitScript(({ projectId, auth }) => {
  window.localStorage.setItem("ppt_god_last_project_id", projectId);
  window.sessionStorage.setItem("pptgod.mvpAuth", JSON.stringify(auth));
}, { projectId: project.id, auth: testerAuth });

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
  if (method === "GET" && pathName === `/projects/${project.id}/slides`) return route.fulfill({ json: slides });
  if (method === "GET" && pathName === `/projects/${project.id}/reference-images`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/documents`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/template-pages`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/workflow-status`) return route.fulfill({ json: workflowStatus() });
  if (method === "GET" && pathName === `/projects/${project.id}/generation-progress`) {
    return route.fulfill({ json: { project_id: project.id, project_status: project.status } });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/status`) return route.fulfill({ json: workflowStatus() });
  if (method === "PATCH" && pathName === `/projects/${project.id}/slides/content`) {
    const payload = request.postDataJSON();
    contentPatches.push(payload.content_json);
    slides[0].content_json = { ...slides[0].content_json, ...payload.content_json };
    const body = payload.content_json?.text_content?.body || "";
    return route.fulfill({
      json: {
        message: "Slide content updated",
        page_num: 1,
        slide_id: slides[0].id,
        visual_directive_suggestions: visualDirectiveSuggestions(body),
      },
    });
  }

  if (pathName.startsWith("/projects") || pathName.startsWith("/auth")) {
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock route: ${method} ${pathName}` } });
  }
  return route.continue();
});

try {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByText("Visual Directive E2E").first().waitFor({ timeout: 10_000 });
  await page.getByText("增长闭环").first().click();
  await page.locator("textarea").first().fill("增长闭环 E2E");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  for (let i = 0; i < 50 && contentPatches.length === 0; i += 1) {
    await page.waitForTimeout(100);
  }
  assert.ok(contentPatches.length > 0, "saving edited content should send a content patch");
  await page.getByText("发现可能的画面要求").waitFor({ timeout: 10_000 });
  await page.getByText(/图示标签[\s\S]*获客[\s\S]*推荐/).waitFor({ timeout: 10_000 });
  await page.getByRole("button", { name: "移到画面要求" }).click();
  await page.getByText("已移到画面要求").waitFor({ timeout: 10_000 });

  assert.ok(contentPatches.length >= 2, "confirming a suggestion should submit a second content patch");
  const confirmedPatch = contentPatches.at(-1);
  assert.match(confirmedPatch.text_content.body, /增长的关键在于四个动作形成闭环/);
  assert.doesNotMatch(confirmedPatch.text_content.body, /用增长飞轮表示/);
  assert.match(confirmedPatch.visual_suggestion, /用增长飞轮表示/);
  assert.deepEqual(confirmedPatch.visual_requirements[0].diagram_labels, ["获客", "激活", "留存", "推荐"]);
  console.log("Visual directive E2E passed.");
} finally {
  await browser.close();
  if (previewChild) previewChild.kill("SIGTERM");
}
