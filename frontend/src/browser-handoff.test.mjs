import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const authGate = readFileSync(join(import.meta.dirname, "AuthGate.tsx"), "utf8");
const app = readFileSync(join(import.meta.dirname, "App.tsx"), "utf8");
const client = readFileSync(join(import.meta.dirname, "api/client.ts"), "utf8");
const viteConfig = readFileSync(join(import.meta.dirname, "../vite.config.ts"), "utf8");

assert.match(
  client,
  /redeemBrowserHandoff[\s\S]*\/auth\/browser-handoff\/redeem[\s\S]*project_id:\s*projectId/,
  "the browser must exchange the one-time handoff with the backend instead of trusting URL account data",
);
assert.match(
  authGate,
  /browserRouteRef\.current\?\.handoffToken\s*\?\s*null\s*:\s*getInitialAuth\(\)/,
  "a CLI handoff must take precedence over stale browser auth",
);
assert.match(
  authGate,
  /redeemBrowserHandoff\(route\.handoffToken, route\.projectId\)[\s\S]*saveStoredAuth\(nextAuth\)[\s\S]*ppt_god_last_project_id[\s\S]*saveAgentContext\(result\.agentContext\)[\s\S]*removeHandoffTokenFromAddressBar\(\)/,
  "a successful exchange must establish the web session, preserve Agent capabilities, select the bound project, and remove the secret from the address bar",
);
assert.match(
  client,
  /CAPABILITY_REQUIRED_EVENT[\s\S]*missing_model_capability[\s\S]*agent_action_required[\s\S]*dispatchEvent/,
  "structured capability failures must reach the global setup guide instead of remaining buried in a workflow panel",
);
assert.match(
  authGate,
  /handleCapabilityRequired[\s\S]*agentContext\?\.textGeneration[\s\S]*agentContext\?\.imageGeneration[\s\S]*交给 \{agentContext\?\.agentName/,
  "the GUI must distinguish configure-it-yourself from returning to an Agent that declared the missing capability",
);
assert.match(
  authGate,
  /catch[\s\S]*clearStoredAuth\(\)[\s\S]*removeHandoffTokenFromAddressBar\(\)[\s\S]*setAuth\(null\)/,
  "a failed exchange must not fall through to an unrelated browser account",
);
assert.match(
  app,
  /const savedProjectId = initialProjectId \|\| localStorage\.getItem\("ppt_god_last_project_id"\)/,
  "a deep-linked project must win over the browser's previously selected project",
);
assert.match(
  app,
  /initialStage === "content"[\s\S]*setCurrentAgentRole\("content"\)[\s\S]*initialStage === "visual"[\s\S]*setCurrentAgentRole\("visual"\)/,
  "content and visual CLI stages must open the corresponding Web workspace",
);
assert.match(
  viteConfig,
  /base:\s*['"]\/['"]/,
  "nested project routes must load root-relative frontend assets instead of the SPA HTML fallback",
);

console.log("browser handoff regression checks passed");
