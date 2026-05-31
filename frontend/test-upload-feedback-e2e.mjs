import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_URL = process.env.APP_URL || "http://127.0.0.1:4177";
const API_BASE = process.env.VITE_API_BASE_URL || APP_URL;
const APP_ORIGIN = new URL(APP_URL).origin;
const API_ORIGIN = new URL(API_BASE, APP_URL).origin;
const testerAuth = {
  testerId: "55555555-5555-4555-8555-555555555555",
  displayName: "Upload Feedback Tester",
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

async function startDevServer() {
  const viteJs = path.join(__dirname, "node_modules", "vite", "bin", "vite.js");
  const appUrl = new URL(APP_URL);
  const proc = spawn(
    process.execPath,
    [viteJs, "--host", appUrl.hostname, "--port", appUrl.port || "4177", "--strictPort"],
    { cwd: __dirname, env: { ...process.env, VITE_API_BASE_URL: "" }, stdio: "ignore" },
  );
  for (let i = 0; i < 60; i += 1) {
    if (await isAppReachable(APP_URL)) return proc;
    if (proc.exitCode !== null) throw new Error(`vite dev server exited before ${APP_URL} was ready.`);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  proc.kill("SIGTERM");
  throw new Error(`Timed out waiting for dev server at ${APP_URL}`);
}

const project = {
  id: "upload-feedback-project",
  title: "Upload Feedback E2E",
  status: "prompt_ready",
  style_id: null,
  style_proposal: null,
  selected_style: { name: "Upload Test Style" },
  selected_template_recommendations: null,
  intent_contract: { audience: "管理层", goal: "测试上传提示", page_count: "1 页" },
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
      text_content: { headline: "上传提示测试", subhead: "", body: "" },
      content_blocks: [],
    },
    visual_json: { page_num: 1, visual_description: "干净的商务封面" },
    prompt_text: "Mock prompt",
    image_path: null,
    reference_images: [],
  },
];

let uploadedLogo = null;
let resolveUpload = null;
let uploadRequestStarted = null;
const uploadGate = new Promise((resolve) => {
  resolveUpload = resolve;
});
const uploadStarted = new Promise((resolve) => {
  uploadRequestStarted = resolve;
});

let devChild = null;
if (!(await isAppReachable(APP_URL))) {
  devChild = await startDevServer();
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
  if (method === "GET" && pathName === "/projects") return route.fulfill({ json: [project] });
  if (method === "GET" && pathName === `/projects/${project.id}`) return route.fulfill({ json: project });
  if (method === "GET" && pathName === `/projects/${project.id}/slides`) return route.fulfill({ json: slides });
  if (method === "GET" && pathName === `/projects/${project.id}/reference-images`) {
    return route.fulfill({ json: uploadedLogo ? [uploadedLogo] : [] });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/documents`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/template-pages`) return route.fulfill({ json: [] });
  if (method === "GET" && pathName === `/projects/${project.id}/template-status`) {
    return route.fulfill({ json: { status: "idle" } });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/generation-progress`) {
    return route.fulfill({ json: { project_id: project.id, project_status: project.status, active_run: null } });
  }
  if (method === "GET" && pathName === `/projects/${project.id}/generation-status`) {
    return route.fulfill({ json: { status: "idle", project_status: project.status, active_run: null } });
  }
  if (method === "GET" && (pathName === `/projects/${project.id}/workflow-status` || pathName === `/projects/${project.id}/status`)) {
    return route.fulfill({
      json: {
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
        quality_report: null,
        has_pptx: false,
        slides: slides.map((slide) => ({
          id: slide.id,
          page_num: slide.page_num,
          status: slide.status,
          error_msg: null,
          stale_flags: {},
        })),
      },
    });
  }
  if (method === "GET" && pathName.match(new RegExp(`^/projects/${project.id}/slides/[^/]+/versions$`))) {
    return route.fulfill({ json: [] });
  }
  if (method === "POST" && pathName === `/projects/${project.id}/upload`) {
    uploadRequestStarted();
    await uploadGate;
    uploadedLogo = {
      id: "logo-ref-1",
      role: "logo",
      url: "/outputs/upload-feedback/logo.png",
      asset_name: "brand-logo-large",
      logo_anchor: "top-right",
      review_status: "user_confirmed",
      file_exists: true,
    };
    return route.fulfill({ json: uploadedLogo });
  }

  if (pathName.startsWith("/projects") || pathName.startsWith("/auth")) {
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock route: ${method} ${pathName}` } });
  }
  return route.continue();
});

try {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  await page.getByText("Upload Feedback E2E").first().waitFor({ timeout: 10_000 });

  await page.getByRole("button", { name: /素材库/ }).click();
  await page.getByText("已添加").waitFor({ timeout: 10_000 });

  const chooserPromise = page.waitForEvent("filechooser");
  await page.locator(".pg-tray-upload-button").filter({ hasText: "Logo" }).first().click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: "brand-logo-large.png",
    mimeType: "image/png",
    buffer: onePixelPng,
  });

  await uploadStarted;
  const status = page.locator('[role="status"]').filter({ hasText: /正在上传 Logo/ }).first();
  await status.waitFor({ timeout: 3000 });
  await page.getByText("brand-logo-large.png").first().waitFor({ timeout: 3000 });
  const activeLogoButton = page.locator('.pg-tray-upload-button[aria-busy="true"]').first();
  assert.equal(
    await activeLogoButton.isDisabled(),
    true,
    "Logo upload button should be disabled while the upload is pending",
  );

  resolveUpload();
  await page.getByText("品牌 Logo 已添加").waitFor({ timeout: 10_000 });
  await status.waitFor({ state: "detached", timeout: 10_000 });

  assert.deepEqual(pageErrors, [], "browser page should not throw runtime errors");
  console.log("Upload feedback E2E passed.");
} finally {
  await browser.close();
  if (devChild) devChild.kill("SIGTERM");
}
