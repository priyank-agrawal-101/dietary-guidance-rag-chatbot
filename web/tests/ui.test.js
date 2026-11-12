import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { JSDOM } from "jsdom";
import { mount } from "../assets/app.js";
import { askQuestion } from "../assets/client.js";
import {
  renderOutcome,
  safeSourceURL,
  validOutcome,
  ERROR_LABELS,
} from "../assets/render.js";

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
const review = JSON.parse(
  await readFile(
    new URL("../../docs/api-review.json", import.meta.url),
    "utf8",
  ),
);
const examples = Object.fromEntries(
  review.cases.map((row) => [row.case, row.response]),
);
const docs = new Map(
  examples.comparison.citations.concat(examples.storage.citations).map((c) => [
    c.document_id,
    {
      document_id: c.document_id,
      document_name: c.document_name,
      source_url: c.source_url,
      publisher: c.publisher,
      format: c.format,
      jurisdiction: c.jurisdiction,
      available: true,
    },
  ]),
);
const selected = {
  mode: "selected",
  document_ids: ["cdc_food_safety", "mn_safe_grilling_pdf"],
};
const tick = async () => {
  for (let i = 0; i < 5; i++)
    await new Promise((resolve) => setImmediate(resolve));
};
const clone = (value) => structuredClone(value);
function response(
  body,
  status = 200,
  id = body.request_id || "server_request",
) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "X-Request-ID": id }),
    json: async () => body,
  };
}
function setup(
  handler = async () => response(clone(examples.storage)),
  overrides = {},
) {
  const dom = new JSDOM(html, { url: "http://localhost:8001/" });
  const calls = [];
  const metadata = [
    ...docs.values(),
    {
      ...docs.values().next().value,
      document_id: "unavailable",
      document_name: "Unavailable reference",
      available: false,
    },
  ];
  const fetcher = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === "/chat") return handler(path, options);
    if (Object.hasOwn(overrides, path)) return overrides[path]();
    const body = {
      "/ui/config": {
        question_max_characters: 2000,
        selected_document_limit: 2,
        request_timeout_seconds: 60,
      },
      "/health/live": { status: "alive" },
      "/health/ready": { status: "ready", corpus_version: "snapshot_fixture" },
      "/documents": { corpus_version: "snapshot_fixture", documents: metadata },
      "/usage": {
        windows: {
          hour: {
            requests: 1,
            tokens: 500,
            request_limit: 60,
            token_limit: 30000,
          },
          day: {
            requests: 2,
            tokens: 876,
            request_limit: 300,
            token_limit: 100000,
          },
        },
      },
    }[path];
    assert.ok(body, `unexpected request: ${path}`);
    return response(clone(body));
  };
  const app = mount(dom.window.document, fetcher);
  const byId = (id) => dom.window.document.getElementById(id);
  function fill(text = "How should I store leftovers?") {
    byId("question").value = text;
    byId("question").dispatchEvent(new dom.window.Event("input"));
  }
  function submit() {
    byId("question-form").dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true }),
    );
  }
  return { dom, app, byId, calls, fill, submit };
}

for (const url of [
  "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "http://example.gov/doc",
  "https://example.gov.evil.test/doc",
  "https://user:pass@example.gov/doc",
  "https://example.gov/other",
  "https://example.gov/doc?leak=1",
  "//example.gov/doc",
]) {
  test(`unsafe citation URL rejected: ${url}`, () =>
    assert.equal(safeSourceURL(url, "https://example.gov/doc"), null));
}
test("reviewed source fragments and PDF page links are allowed", () => {
  assert.equal(
    safeSourceURL("https://example.gov/doc#page=2", "https://example.gov/doc"),
    "https://example.gov/doc#page=2",
  );
});

test("comparison preserves document separation, PDF physical pages and canonical claims", () => {
  const doc = new JSDOM("").window.document;
  const node = renderOutcome(doc, examples.comparison, {
    documents: docs,
    scope: selected,
  });
  assert.equal(node.querySelectorAll(".source-answer").length, 2);
  assert.deepEqual(
    [...node.querySelectorAll(".claim-text")].map((n) => n.textContent),
    examples.comparison.document_answers.flatMap((a) =>
      a.claims.map((c) => c.text),
    ),
  );
  assert.ok(node.textContent.includes("Physical PDF pages:"));
  for (const link of node.querySelectorAll("a"))
    assert.equal(link.rel, "noopener noreferrer");
});

test("partial comparison shows an empty source card and its explicit gap", () => {
  const body = clone(examples.comparison);
  body.document_answers = body.document_answers.slice(0, 1);
  body.citations = body.citations.filter(
    (c) => c.document_id === body.document_answers[0].document_id,
  );
  body.unanswered_aspects = [
    "Minnesota (mn_safe_grilling_pdf): requested storage guidance; no sufficient retrieved evidence.",
  ];
  assert.ok(validOutcome(body, true));
  const node = renderOutcome(new JSDOM("").window.document, body, {
    documents: docs,
    scope: selected,
  });
  assert.equal(node.querySelectorAll(".source-answer").length, 2);
  assert.ok(node.textContent.includes("Partly supported"));
  assert.ok(node.textContent.includes("Evidence gap"));
  assert.ok(
    node.textContent.includes(
      "requested storage guidance; no sufficient retrieved evidence.",
    ),
  );
});

test("source, quote, question and claim markup stays literal; hostile links are inert", async () => {
  const body = clone(examples.storage);
  const malicious =
    '<img src=x onerror="window.pwned=true"><script>window.pwned=true</script>';
  body.document_answers[0].claims[0].text = malicious;
  body.citations[0].support_quotes = [malicious];
  body.citations[0].document_name = malicious;
  body.citations[0].source_url = "javascript:alert(1)";
  const app = setup(async () => response(body));
  await app.app.initialized;
  app.fill(malicious);
  app.submit();
  await tick();
  const node = app.byId("conversation");
  assert.equal(node.querySelectorAll("img, script, iframe").length, 0);
  assert.equal(node.querySelectorAll("a").length, 0);
  assert.ok(node.textContent.includes(malicious));
  assert.equal(app.dom.window.pwned, undefined);
});

for (const status of [
  "not_covered",
  "out_of_scope",
  "requires_structured_data",
  "needs_clarification",
]) {
  test(`renders conversational state: ${status}`, () => {
    const body = {
      request_id: "req",
      status,
      message: "Fixed application message.",
      citations: [],
      document_answers: [],
      searched_documents: [],
      unanswered_aspects: [],
    };
    assert.ok(validOutcome(body, true));
    const node = renderOutcome(new JSDOM("").window.document, body, {
      documents: docs,
      scope: { mode: "all" },
    });
    assert.ok(
      node.textContent.includes(
        status === "not_covered"
          ? "Try a more specific food question"
          : body.message,
      ),
    );
    assert.equal(node.querySelectorAll(".claim").length, 0);
  });
}
for (const code of Object.keys(ERROR_LABELS)) {
  test(`renders typed service error: ${code}`, () => {
    const body = {
      request_id: "req",
      code,
      message: "Sanitized error.",
      retry_after_seconds: code === "usage_limit_exceeded" ? 90 : null,
    };
    assert.ok(validOutcome(body, false));
    const node = renderOutcome(new JSDOM("").window.document, body, {
      documents: docs,
      scope: { mode: "all" },
    });
    assert.ok(node.textContent.includes(ERROR_LABELS[code]));
    assert.equal(node.querySelectorAll(".claim").length, 0);
    if (code === "usage_limit_exceeded")
      assert.ok(node.textContent.includes("90 seconds"));
  });
}

test("all-corpus form posts just the current question and scope; examples do not submit", async () => {
  const ui = setup();
  await ui.app.initialized;
  ui.dom.window.document.querySelector("[data-example]").click();
  assert.equal(ui.calls.filter((c) => c.path === "/chat").length, 0);
  ui.fill("First question");
  ui.submit();
  await tick();
  ui.fill("Second question");
  ui.submit();
  await tick();
  const posts = ui.calls.filter((c) => c.path === "/chat");
  assert.deepEqual(JSON.parse(posts[1].options.body), {
    question: "Second question",
    scope: { mode: "all" },
  });
  assert.equal(ui.byId("conversation").children.length, 2);
  ui.byId("clear-chat").click();
  assert.equal(ui.byId("conversation").children.length, 0);
  assert.equal(ui.byId("empty-state").hidden, false);
  assert.equal(ui.dom.window.localStorage.length, 0);
});

test("selected comparison posts exact document IDs and prevents empty selections", async () => {
  const ui = setup(async () => response(clone(examples.comparison)));
  await ui.app.initialized;
  const radio = ui.dom.window.document.querySelector("input[value=selected]");
  radio.click();
  ui.fill();
  ui.submit();
  await tick();
  assert.equal(ui.calls.filter((c) => c.path === "/chat").length, 0);
  assert.ok(ui.byId("form-error").textContent.includes("at least one"));
  for (const id of selected.document_ids)
    ui.dom.window.document.querySelector(`input[value="${id}"]`).click();
  ui.submit();
  await tick();
  const payload = JSON.parse(
    ui.calls.find((c) => c.path === "/chat").options.body,
  );
  assert.deepEqual(payload.scope, selected);
  assert.equal(
    ui.byId("conversation").querySelectorAll(".source-answer").length,
    2,
  );
});

test("source filtering, unavailable exclusion and selected-source budget work", async () => {
  const ui = setup();
  await ui.app.initialized;
  ui.dom.window.document.querySelector("input[value=selected]").click();
  const boxes = [...ui.byId("source-list").querySelectorAll("input")];
  boxes.forEach((input) => input.click());
  assert.equal(ui.app.state.selected.size, 2);
  assert.ok(ui.byId("selection-error").textContent.includes("up to 2"));
  assert.equal(
    ui.byId("source-list").querySelector("input[value=unavailable]"),
    null,
  );
  ui.byId("source-filter").value = "no such source";
  ui.byId("source-filter").dispatchEvent(new ui.dom.window.Event("input"));
  assert.equal(ui.byId("no-matches").hidden, false);
});

test("a source refresh preserves missing selected IDs and blocks silent scope changes", async () => {
  let missing = false;
  const ui = setup(undefined, {
    "/documents": () =>
      response({
        corpus_version: "snapshot_fixture",
        documents: [...docs.values()].map((d) => ({
          ...d,
          available: missing ? false : true,
        })),
      }),
  });
  await ui.app.initialized;
  ui.dom.window.document.querySelector("input[value=selected]").click();
  ui.dom.window.document.querySelector("input[value=mn_storage_chart]").click();
  missing = true;
  ui.byId("retry-sources").click();
  await tick();
  assert.ok(ui.app.state.selected.has("mn_storage_chart"));
  ui.fill();
  ui.submit();
  await tick();
  assert.equal(ui.calls.filter((c) => c.path === "/chat").length, 0);
  assert.ok(ui.byId("form-error").textContent.includes("available source"));
});

test("pending answer is hidden, duplicate submit is blocked, and late results cannot repopulate a cleared chat", async () => {
  let finish;
  const ui = setup(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await ui.app.initialized;
  ui.fill();
  ui.submit();
  ui.submit();
  await tick();
  assert.equal(ui.calls.filter((c) => c.path === "/chat").length, 1);
  assert.equal(ui.byId("conversation").querySelectorAll(".claim").length, 0);
  assert.ok(ui.byId("ask").disabled);
  ui.byId("clear-chat").click();
  finish(response(clone(examples.storage)));
  await tick();
  assert.equal(ui.byId("conversation").children.length, 0);
});

test("stop waiting cancels the request and shows no answer", async () => {
  const ui = setup(
    (path, options) =>
      new Promise((resolve, reject) => {
        options.signal.addEventListener(
          "abort",
          () => reject(new Error("Aborted")),
          { once: true },
        );
      }),
  );
  await ui.app.initialized;
  ui.fill();
  ui.submit();
  await tick();
  ui.byId("cancel").click();
  await tick();
  assert.ok(ui.byId("conversation").textContent.includes("Stopped waiting"));
  assert.equal(ui.byId("conversation").querySelectorAll(".claim").length, 0);
  assert.equal(ui.byId("ask").disabled, false);
});

test("malformed responses, unknown statuses and mismatched request IDs are withheld", async () => {
  for (const body of [
    { request_id: "r", status: "made_up" },
    { ...clone(examples.storage), request_id: "different" },
  ]) {
    await assert.rejects(
      () =>
        askQuestion(async () => response(body, 200, "server_id"), {
          question: "test",
        }),
      (error) => error.code === "invalid_response",
    );
  }
  const malformed = clone(examples.storage);
  malformed.document_answers[0].claims[0].citation_ids = ["unknown"];
  assert.equal(validOutcome(malformed, true), false);
  await assert.rejects(
    () =>
      askQuestion(
        async () => ({
          ok: true,
          headers: new Headers(),
          json: async () => {
            throw new Error("secret");
          },
        }),
        {},
      ),
    (error) =>
      error.code === "invalid_response" && !error.message.includes("secret"),
  );
});

test("network failures and local deadlines are explicit service failures", async () => {
  await assert.rejects(
    () =>
      askQuestion(async () => {
        throw new Error("private detail");
      }, {}),
    (error) =>
      error.code === "network_error" && !error.message.includes("private"),
  );
  await assert.rejects(
    () =>
      askQuestion(
        (path, options) =>
          new Promise((resolve, reject) => {
            options.signal.addEventListener("abort", () =>
              reject(new Error("aborted")),
            );
          }),
        {},
        { timeout: 10 },
      ),
    (error) => error.code === "request_timeout",
  );
});

test("corpus and model readiness failures stay visible; source listing can still work", async () => {
  for (const code of ["corpus_unavailable", "model_unavailable"]) {
    const ui = setup(undefined, {
      "/health/ready": () => response({ code }, 503),
      "/documents": () =>
        response(
          {
            corpus_version:
              code === "corpus_unavailable" ? null : "snapshot_fixture",
            documents: [...docs.values()].map((d) => ({
              ...d,
              available: code !== "corpus_unavailable",
            })),
            error: code === "corpus_unavailable" ? { code } : null,
          },
          code === "corpus_unavailable" ? 503 : 200,
        ),
    });
    await ui.app.initialized;
    assert.equal(ui.byId("health-notice").hidden, false);
    assert.ok(ui.byId("health-notice").textContent.includes("unavailable"));
    ui.fill();
    assert.equal(ui.byId("ask").disabled, false);
    if (code === "corpus_unavailable")
      assert.equal(ui.byId("source-list").children.length, 0);
  }
});

test("configured limits are used without loading diagnostic usage", async () => {
  const ui = setup(undefined, {
    "/ui/config": () =>
      response({
        question_max_characters: 20,
        selected_document_limit: 1,
        request_timeout_seconds: 30,
      }),
    "/usage": () => response({ code: "model_unavailable" }, 503),
  });
  await ui.app.initialized;
  assert.equal(ui.byId("question").maxLength, 20);
  ui.fill("x".repeat(21));
  ui.submit();
  await tick();
  assert.equal(ui.calls.filter((c) => c.path === "/chat").length, 0);
  ui.fill("How to store eggs?");
  ui.submit();
  await tick();
  assert.ok(ui.byId("conversation").querySelector(".claim"));
  assert.equal(
    ui.calls.some((c) => c.path === "/usage"),
    false,
  );
});

test("busy source listing is distinguished from an unreachable server", async () => {
  const ui = setup(undefined, {
    "/documents": () =>
      response({ code: "service_busy", message: "Service is busy." }, 503),
  });
  await ui.app.initialized;
  assert.equal(ui.byId("availability").hidden, false);
  assert.ok(
    ui.byId("health-notice").textContent.includes("taking a little longer"),
  );
  assert.equal(ui.calls.filter((call) => call.path === "/documents").length, 2);
});

test("unknown citation URLs and cross-document references cannot authorize an answer", () => {
  const body = clone(examples.comparison);
  body.document_answers[0].claims[0].citation_ids =
    body.document_answers[1].claims[0].citation_ids;
  assert.equal(validOutcome(body, true), false);
  assert.equal(safeSourceURL("https://example.gov/doc", undefined), null);
});

test("unavailable UI configuration cannot be bypassed by refreshing status", async () => {
  const ui = setup(undefined, { "/ui/config": () => response({}, 503) });
  await ui.app.initialized;
  ui.fill();
  ui.byId("retry-sources").click();
  await tick();
  ui.submit();
  await tick();
  assert.equal(ui.app.state.initialized, false);
  assert.equal(ui.byId("ask").disabled, true);
  assert.equal(ui.calls.filter((c) => c.path === "/chat").length, 0);
});

test("gap rendering removes duplicated scope while retaining every searched source", () => {
  const body = clone(examples.comparison);
  body.unanswered_aspects = [
    "Example (mn_storage_chart): requested storage; no sufficient retrieved evidence. Searched: huge repeated scope.",
  ];
  const node = renderOutcome(new JSDOM("").window.document, body, {
    documents: docs,
    scope: selected,
  });
  assert.ok(
    node.textContent.includes(
      "requested storage; no sufficient retrieved evidence.",
    ),
  );
  const notice = node.querySelector(".notice");
  assert.ok(!notice.textContent.includes("huge repeated scope"));
  const details = node.querySelector(".diagnostic-json").parentElement;
  assert.equal(details.open, false);
  assert.ok(details.textContent.includes("huge repeated scope"));
  for (const id of body.searched_documents)
    assert.ok(node.textContent.includes(docs.get(id).document_name));
});

test("a typed HTTP error cannot smuggle answer fields into the renderer", () => {
  const body = {
    ...clone(examples.storage),
    code: "model_unavailable",
    message: "Dependency failed.",
  };
  assert.equal(validOutcome(body, false), false);
});

test("a response cannot broaden an explicit document selection", async () => {
  await assert.rejects(
    () =>
      askQuestion(async () => response(clone(examples.comparison)), {
        question: "question",
        scope: { mode: "selected", document_ids: ["cdc_food_safety"] },
      }),
    (error) => error.code === "invalid_response",
  );
});

test("chat shows evidence without diagnostic metadata or usage requests", async () => {
  const ui = setup();
  await ui.app.initialized;
  ui.fill();
  ui.submit();
  await tick();
  const result = ui.byId("conversation");
  assert.ok(result.querySelector(".claim"));
  assert.equal(result.textContent.includes("Request ID:"), false);
  assert.equal(result.textContent.includes("Source collection:"), false);
  assert.equal(ui.byId("usage-content"), null);
  assert.equal(ui.byId("unavailable-list"), null);
  assert.equal(
    ui.calls.some((call) => call.path === "/usage"),
    false,
  );
  assert.equal(
    ui.dom.window.document.querySelector("footer a").getAttribute("href"),
    "/diagnostics",
  );
});

test("source loading recovers from a brief busy response without replaying chat", async () => {
  let listingCalls = 0;
  const ui = setup(undefined, {
    "/documents": () =>
      ++listingCalls === 1
        ? response(
            { code: "service_busy", message: "Internal capacity details." },
            503,
          )
        : response({
            documents: [...docs.values()],
            corpus_version: "snapshot_fixture",
          }),
  });
  await ui.app.initialized;
  assert.equal(ui.byId("availability").hidden, true);
  assert.equal(ui.app.state.documents.size, docs.size);
  assert.equal(listingCalls, 2);
  assert.equal(
    ui.calls.some((call) => call.path === "/chat"),
    false,
  );
  const paths = ui.calls.map((call) => call.path);
  assert.ok(paths.indexOf("/health/ready") < paths.indexOf("/documents"));
});

test("source-gap replies keep raw source identifiers only in collapsed diagnostics", () => {
  const body = {
    request_id: "req-gap",
    status: "not_covered",
    message:
      "The retrieved guidance does not cover this: internal_source_1 (internal_source_1).",
    corpus_version: "snapshot_internal",
    searched_documents: ["internal_source_1"],
    document_answers: [],
    citations: [],
    unanswered_aspects: [],
  };
  const node = renderOutcome(new JSDOM("").window.document, body, {
    documents: new Map(),
    scope: { mode: "all" },
  });
  assert.ok(
    node
      .querySelector(".notice")
      .textContent.includes("Try a more specific food question"),
  );
  assert.equal(
    node.querySelector(".notice").textContent.includes("internal_source_1"),
    false,
  );
  const diagnostics = node.querySelector(".diagnostic-json");
  assert.ok(diagnostics.textContent.includes("internal_source_1"));
  assert.equal(diagnostics.parentElement.open, false);
});
