import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { JSDOM } from "jsdom";
import { mountDiagnostics } from "../assets/diagnostics.js";

const html = await readFile(
  new URL("../diagnostics.html", import.meta.url),
  "utf8",
);
const tick = async () => {
  for (let i = 0; i < 6; i++)
    await new Promise((resolve) => setImmediate(resolve));
};
function setup(overrides = {}) {
  const dom = new JSDOM(html, { url: "http://localhost/diagnostics" });
  const calls = [];
  const window = {
    requests: 2,
    tokens: 876,
    pending_calls: 1,
    request_limit: 60,
    token_limit: 30000,
  };
  const bodies = {
    "/ui/config": { request_timeout_seconds: 60 },
    "/health/live": { status: "alive" },
    "/health/ready": { status: "ready", corpus_version: "snapshot" },
    "/documents": {
      corpus_version: "snapshot",
      documents: [
        {
          document_name: "<img src=x onerror=alert(1)>",
          document_id: "one",
          publisher: "Publisher",
          available: false,
        },
      ],
    },
    "/usage": {
      windows: { minute: window, hour: window, day: window },
      retry_after_seconds: 42,
    },
    ...overrides,
  };
  const fetcher = async (path) => {
    calls.push(path);
    assert.notEqual(path, "/chat");
    const body = bodies[path];
    if (body instanceof Error) throw body;
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => body,
    };
  };
  const app = mountDiagnostics(dom.window.document, fetcher);
  const byId = (id) => dom.window.document.getElementById(id);
  return { dom, app, calls, byId };
}

test("diagnostics tabs load checks on demand and retain results", async () => {
  const ui = setup();
  await ui.app.initialized;
  assert.deepEqual(ui.calls, ["/ui/config", "/health/live", "/health/ready"]);
  assert.match(ui.byId("status-content").textContent, /ready/);
  ui.byId("tab-usage").click();
  await tick();
  assert.equal(ui.byId("panel-status").hidden, true);
  assert.equal(ui.byId("panel-usage").hidden, false);
  assert.equal(ui.byId("tab-usage").getAttribute("aria-selected"), "true");
  assert.match(
    ui.byId("usage-content").textContent,
    /876 \/ 30,000 tokens · 1 pending/,
  );
  assert.match(ui.byId("usage-content").textContent, /42 seconds/);
  assert.equal(ui.byId("usage-content").querySelectorAll("meter").length, 3);
  ui.byId("tab-status").click();
  ui.byId("tab-usage").click();
  await tick();
  assert.equal(ui.calls.filter((path) => path === "/usage").length, 1);
});

test("tabs support keyboard navigation and focus", async () => {
  const ui = setup();
  await ui.app.initialized;
  ui.byId("tab-status").dispatchEvent(
    new ui.dom.window.KeyboardEvent("keydown", {
      key: "ArrowLeft",
      bubbles: true,
    }),
  );
  assert.equal(ui.dom.window.document.activeElement.id, "tab-api");
  assert.equal(ui.byId("panel-api").hidden, false);
  ui.byId("tab-api").dispatchEvent(
    new ui.dom.window.KeyboardEvent("keydown", { key: "Home", bubbles: true }),
  );
  assert.equal(ui.dom.window.document.activeElement.id, "tab-status");
  assert.equal(ui.byId("tab-api").tabIndex, -1);
});

test("source metadata remains inert text and unavailable references stay diagnostic", async () => {
  const ui = setup();
  await ui.app.initialized;
  ui.byId("tab-sources").click();
  await tick();
  assert.match(
    ui.byId("sources-content").textContent,
    /0 available · 1 unavailable/,
  );
  assert.match(ui.byId("sources-content").textContent, /<img src=x/);
  assert.equal(ui.byId("sources-content").querySelector("img"), null);
});

test("offline usage and malformed usage have clear states with manual retry", async () => {
  for (const body of [
    { generation: "offline", usage: null },
    { windows: {} },
  ]) {
    const ui = setup({ "/usage": body });
    await ui.app.initialized;
    ui.byId("tab-usage").click();
    await tick();
    assert.match(
      ui.byId("usage-content").textContent,
      body.generation ? /No Groq calls/ : /unavailable/,
    );
    const button = ui.dom.window.document.querySelector(
      '[data-refresh="usage"]',
    );
    assert.equal(button.disabled, false);
    button.click();
    await tick();
    assert.equal(ui.calls.filter((path) => path === "/usage").length, 2);
  }
});

test("network failures do not prevent opening the API panel", async () => {
  const ui = setup({ "/ui/config": new Error("offline") });
  await ui.app.initialized;
  assert.match(ui.byId("status-content").textContent, /unavailable/);
  ui.byId("tab-api").click();
  assert.equal(ui.byId("panel-api").hidden, false);
  assert.equal(
    ui.byId("panel-api").querySelector("a").getAttribute("href"),
    "/docs",
  );
});
