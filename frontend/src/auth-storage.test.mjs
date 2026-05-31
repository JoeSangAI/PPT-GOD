import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";

function makeStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

function loadClient({ localStorage, sessionStorage }) {
  const sourcePath = join(import.meta.dirname, "api/client.ts");
  const source = readFileSync(sourcePath, "utf8").replaceAll("import.meta.env", "__env");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;

  const sandbox = {
    __env: { DEV: false, PROD: true },
    exports: {},
    module: { exports: {} },
    localStorage,
    sessionStorage,
    window: { location: { origin: "https://ppt.example.test" }, fetch() {} },
  };
  sandbox.module.exports = sandbox.exports;
  vm.runInNewContext(compiled, sandbox, { filename: sourcePath });
  return sandbox.module.exports;
}

const legacyAuth = JSON.stringify({
  testerId: "11111111-1111-4111-8111-111111111111",
  displayName: "阿桑",
});

{
  const localStorage = makeStorage({ "pptgod.mvpAuth": legacyAuth });
  const sessionStorage = makeStorage();
  const { getStoredAuth } = loadClient({ localStorage, sessionStorage });

  assert.equal(getStoredAuth(), null, "legacy localStorage auth must not auto-enter a tester space");
  assert.equal(localStorage.getItem("pptgod.mvpAuth"), null, "legacy localStorage auth should be cleared on read");
}

{
  const localStorage = makeStorage();
  const sessionStorage = makeStorage();
  const auth = {
    testerId: "22222222-2222-4222-8222-222222222222",
    displayName: "朋友A",
  };
  const { saveStoredAuth, getStoredAuth, clearStoredAuth } = loadClient({ localStorage, sessionStorage });

  saveStoredAuth(auth);

  assert.equal(
    JSON.stringify(getStoredAuth()),
    JSON.stringify(auth),
    "current-tab auth should be readable after login",
  );
  assert.equal(localStorage.getItem("pptgod.mvpAuth"), null, "auth must not persist across browser sessions");
  assert.equal(sessionStorage.getItem("pptgod.mvpAuth"), JSON.stringify(auth));

  clearStoredAuth();

  assert.equal(getStoredAuth(), null);
  assert.equal(sessionStorage.getItem("pptgod.mvpAuth"), null);
}
