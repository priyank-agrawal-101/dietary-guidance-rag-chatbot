// Runs the real browser-side controller and DOM renderer against the offline local API.
// jsdom is a test DOM, not a claim of native browser layout verification.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { JSDOM } from "jsdom";
import { mount } from "./assets/app.js";

const base = process.argv[2] || "http://127.0.0.1:8001";
const output = new URL("../docs/ui-review.json", import.meta.url);
const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const dom = new JSDOM(html, { url: base });
const exchanges = [];
const fetcher = async (path, options) => {
  const response = await fetch(new URL(path, base), options);
  const body = await response.clone().json();
  if (path === "/chat")
    exchanges.push({
      request: JSON.parse(options.body),
      http_status: response.status,
      response: body,
    });
  return response;
};
const { initialized, state } = mount(dom.window.document, fetcher);
await initialized;
assert.equal(dom.window.document.getElementById("availability").hidden, true);
const ready = await (await fetch(new URL("/health/ready", base))).json();
assert.equal(
  ready.generation,
  "extractive_v1",
  "review requires the offline server to avoid paid calls",
);
const byId = (id) => dom.window.document.getElementById(id);
const cases = [
  [
    "full-corpus",
    "What internal temperature should chicken be cooked to?",
    [],
    "answered",
  ],
  [
    "named-source",
    "How long can hard cooked eggs stay in the refrigerator or freezer?",
    ["mn_storage_chart"],
    "answered",
  ],
  [
    "comparison",
    "What internal temperature should chicken be cooked to?",
    ["cdc_food_safety", "mn_safe_grilling_pdf"],
    "answered",
  ],
  [
    "partial-comparison",
    "How long can hard cooked eggs stay in the refrigerator?",
    ["mn_storage_chart", "mn_safe_grilling_pdf"],
    "answered",
  ],
  [
    "evidence-gap",
    "What refrigerator storage time does the chart give for TV dinners?",
    ["mn_storage_chart"],
    "not_covered",
  ],
  [
    "clarification",
    "How long can cooked eggs keep?",
    ["mn_storage_chart"],
    "needs_clarification",
  ],
  ["medical-refusal", "What diet treats diabetes?", [], "out_of_scope"],
  [
    "structured-data",
    "How much protein is in an egg?",
    [],
    "requires_structured_data",
  ],
];
const results = [];
for (const [name, question, ids, expected] of cases) {
  byId("clear-selection").click();
  dom.window.document
    .querySelector(
      `input[name=scope][value=${ids.length ? "selected" : "all"}]`,
    )
    .click();
  for (const id of ids)
    dom.window.document
      .querySelector(`input[type=checkbox][value=${id}]`)
      .click();
  byId("question").value = question;
  byId("question").dispatchEvent(new dom.window.Event("input"));
  byId("question-form").dispatchEvent(
    new dom.window.Event("submit", { cancelable: true, bubbles: true }),
  );
  const deadline = Date.now() + 75000;
  while (state.busy && Date.now() < deadline)
    await new Promise((resolve) => setTimeout(resolve, 25));
  assert.ok(!state.busy, "UI request completed");
  const exchange = exchanges.at(-1);
  assert.equal(exchange.response.status, expected);
  assert.deepEqual(
    exchange.request.scope,
    ids.length ? { mode: "selected", document_ids: ids } : { mode: "all" },
  );
  const turn = byId("conversation").lastElementChild;
  assert.deepEqual(
    [...turn.querySelectorAll(".claim-text")].map((n) => n.textContent),
    exchange.response.document_answers.flatMap((a) =>
      a.claims.map((c) => c.text),
    ),
  );
  assert.ok(turn.querySelector("h3"));
  assert.equal(turn.querySelectorAll("script, iframe, img").length, 0);
  const unsafe = [...turn.querySelectorAll("a")].filter(
    (a) => a.protocol !== "https:" || a.rel !== "noopener noreferrer",
  );
  assert.equal(unsafe.length, 0);
  if (name === "partial-comparison") {
    assert.equal(turn.querySelectorAll(".source-answer").length, 2);
    assert.ok(turn.textContent.includes("Evidence gap"));
    assert.ok(exchange.response.unanswered_aspects.length);
  }
  results.push({
    case: name,
    ...exchange,
    rendered_heading: turn.querySelector("h3").textContent,
    rendered_answer_documents: [...turn.querySelectorAll(".source-answer")].map(
      (n) => n.dataset.documentId,
    ),
    rendered_claims: turn.querySelectorAll(".claim-text").length,
    rendered_source_links: turn.querySelectorAll("a").length,
  });
}
const review = {
  version: "ui_review_v1",
  review_date: "2026-11-12",
  corpus:
    "real approved active corpus, BGE/hybrid retrieval and shared validated API",
  renderer:
    "real UI controller and renderer exercised in jsdom; native Chrome inspection documented separately",
  generation: ready.generation,
  groq_calls: 0,
  ready,
  cases: results,
  passed: true,
};
await writeFile(output, JSON.stringify(review, null, 2) + "\n");
console.log(
  JSON.stringify({
    passed: true,
    cases: results.map((c) => ({
      case: c.case,
      status: c.response.status,
      claims: c.rendered_claims,
    })),
    groq_calls: 0,
    output: output.pathname,
  }),
);
