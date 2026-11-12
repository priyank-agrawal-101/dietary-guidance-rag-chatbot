import { askQuestion, fetchJSON } from "./client.js";
import { element, ERROR_LABELS, renderOutcome } from "./render.js";

export function mount(doc, fetcher) {
  const byId = (id) => doc.getElementById(id);
  const state = {
    documents: new Map(),
    selected: new Set(),
    initialized: false,
    configLoaded: false,
    busy: false,
    active: null,
    config: {
      question_max_characters: 2000,
      selected_document_limit: 20,
      request_timeout_seconds: 60,
    },
  };
  const question = byId("question");
  const format = (value) => Number(value).toLocaleString("en-US");
  const scopeMode = () => doc.querySelector("input[name=scope]:checked").value;
  function message(target, text) {
    target.textContent = text;
    target.hidden = !text;
  }

  function updateForm() {
    byId("source-picker").hidden = scopeMode() !== "selected";
    byId("selection-count").textContent = `${state.selected.size} selected`;
    byId("scope-description").textContent =
      scopeMode() === "all"
        ? "Searching all available sources"
        : state.selected.size > 1
          ? `Comparing ${state.selected.size} selected sources`
          : "Searching selected sources";
    byId("character-count").textContent =
      `${format(question.value.length)} / ${format(state.config.question_max_characters)}`;
    byId("ask").disabled =
      !state.initialized || state.busy || !question.value.trim();
    byId("cancel").hidden = !state.busy;
  }

  function renderSources() {
    const list = byId("source-list");
    list.replaceChildren();
    let available = 0;
    for (const source of state.documents.values()) {
      if (!source.available) continue;
      available++;
      const label = element(doc, "label", undefined, "source-choice");
      const input = element(doc, "input");
      input.type = "checkbox";
      input.value = source.document_id;
      input.checked = state.selected.has(source.document_id);
      input.addEventListener("change", () => {
        if (
          input.checked &&
          state.selected.size >= state.config.selected_document_limit
        ) {
          input.checked = false;
          message(
            byId("selection-error"),
            `Choose up to ${state.config.selected_document_limit} sources.`,
          );
          return;
        }
        if (input.checked) state.selected.add(source.document_id);
        else state.selected.delete(source.document_id);
        message(byId("selection-error"), "");
        updateForm();
      });
      const text = element(doc, "span");
      text.append(element(doc, "strong", source.document_name));
      text.append(
        element(
          doc,
          "small",
          `${source.publisher} · ${source.format.toUpperCase()}`,
        ),
      );
      label.dataset.search =
        `${source.document_name} ${source.publisher} ${source.document_id}`.toLowerCase();
      label.append(input, text);
      list.append(label);
    }
    byId("available-count").textContent = `${available} available`;
    filterSources();
  }

  function filterSources() {
    const filter = byId("source-filter").value.trim().toLowerCase();
    let matches = 0;
    for (const label of byId("source-list").children) {
      label.hidden = !label.dataset.search.includes(filter);
      if (!label.hidden) matches++;
    }
    byId("no-matches").hidden = matches > 0;
  }

  async function refreshStatus() {
    if (!state.configLoaded) return;
    byId("retry-sources").disabled = true;
    try {
      const live = await fetchJSON(fetcher, "/health/live");
      if (!live.ok || live.body.status !== "alive")
        throw new Error("Service offline");
      // Status reads are safe to retry. Never automatically replay a chat request.
      async function status(path) {
        const options = {
          timeout: (state.config.request_timeout_seconds + 5) * 1000,
        };
        let result = await fetchJSON(fetcher, path, options);
        if (result.body.code === "service_busy") {
          await new Promise((resolve) => setTimeout(resolve, 250));
          result = await fetchJSON(fetcher, path, options);
        }
        return result;
      }
      const ready = await status("/health/ready");
      const listing = await status("/documents");
      if (!Array.isArray(listing.body.documents)) {
        const error = new Error("Source listing unavailable");
        error.code = listing.body.code || "invalid_response";
        throw error;
      }
      const sources = listing.body.documents;
      if (
        sources.some(
          (source) =>
            !source ||
            typeof source.document_id !== "string" ||
            typeof source.document_name !== "string" ||
            typeof source.publisher !== "string" ||
            !["html", "pdf"].includes(source.format) ||
            typeof source.available !== "boolean",
        )
      )
        throw new Error("Invalid source listing");
      state.documents = new Map(
        sources.map((source) => [
          source.document_id,
          Object.freeze({ ...source }),
        ]),
      );
      // A refresh must never silently shrink or broaden a previously selected scope.
      const missing = [...state.selected].filter(
        (id) => !state.documents.get(id)?.available,
      );
      message(
        byId("selection-error"),
        missing.length
          ? "A previously selected source is unavailable. Clear the selection and choose available sources."
          : "",
      );
      renderSources();
      if (
        ready.ok &&
        listing.ok &&
        ready.body.corpus_version === listing.body.corpus_version
      ) {
        message(byId("health-notice"), "");
      } else {
        const code = ready.body.code || listing.body.error?.code;
        message(
          byId("health-notice"),
          code === "service_busy"
            ? "Sources are taking a little longer to load. Please try again in a moment."
            : code === "request_timeout"
              ? "The source status check timed out. Try loading sources again."
              : code === "model_unavailable"
                ? "Answers are temporarily unavailable. Please try again later."
                : "Sources are temporarily unavailable. Try loading them again before asking about their guidance.",
        );
      }
    } catch (error) {
      message(
        byId("health-notice"),
        error.code === "service_busy"
          ? "Sources are taking a little longer to load. Please try again in a moment."
          : error.code === "request_timeout"
            ? "The source status check timed out. Try loading sources again."
            : "Unable to load sources. Check your connection and try again.",
      );
    } finally {
      byId("retry-sources").disabled = false;
      byId("availability").hidden = byId("health-notice").hidden;
      state.initialized = true;
      updateForm();
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (state.busy || !state.initialized) return;
    const text = question.value.trim();
    const scope =
      scopeMode() === "all"
        ? { mode: "all" }
        : { mode: "selected", document_ids: [...state.selected] };
    if (!text || question.value.length > state.config.question_max_characters) {
      message(
        byId("form-error"),
        `Enter a question of up to ${state.config.question_max_characters} characters.`,
      );
      return;
    }
    if (
      scope.mode === "selected" &&
      (!scope.document_ids.length ||
        scope.document_ids.some((id) => !state.documents.get(id)?.available))
    ) {
      message(
        byId("form-error"),
        "Choose at least one available source, or search all available sources.",
      );
      return;
    }
    message(byId("form-error"), "");
    const turn = element(doc, "article", undefined, "turn");
    const user = element(doc, "div", undefined, "user-question");
    user.append(element(doc, "strong", "You"), element(doc, "p", text));
    turn.append(
      user,
      element(
        doc,
        "p",
        scope.mode === "all"
          ? "All available sources"
          : `${scope.document_ids.length} selected source${scope.document_ids.length === 1 ? "" : "s"}`,
        "turn-scope",
      ),
    );
    const result = element(doc, "div");
    const pending = element(doc, "div", undefined, "pending");
    pending.setAttribute("role", "status");
    pending.append(
      element(doc, "span", undefined, "spinner"),
      element(
        doc,
        "span",
        "Checking the guidance. The completed answer will appear here.",
      ),
    );
    result.append(pending);
    turn.append(result);
    byId("conversation").append(turn);
    turn.scrollIntoView?.({ block: "start" });
    while (byId("conversation").children.length > 10)
      byId("conversation").firstElementChild.remove();
    byId("empty-state").hidden = true;
    byId("clear-chat").hidden = false;
    const context = { documents: new Map(state.documents), scope };
    const active = { controller: new AbortController(), result };
    state.active = active;
    state.busy = true;
    updateForm();
    try {
      const body = await askQuestion(
        fetcher,
        { question: text, scope },
        {
          signal: active.controller.signal,
          timeout: (state.config.request_timeout_seconds + 5) * 1000,
        },
      );
      if (state.active !== active) return;
      result.replaceChildren(renderOutcome(doc, body, context));
    } catch (error) {
      if (state.active !== active) return;
      const box = element(doc, "div", undefined, "notice error");
      box.append(
        element(
          doc,
          "h4",
          ERROR_LABELS[error.code] ||
            (error.code === "cancelled"
              ? "Stopped waiting"
              : "Could not complete the request"),
        ),
      );
      box.append(element(doc, "p", error.message));
      result.replaceChildren(box);
    } finally {
      if (state.active === active) {
        state.active = null;
        state.busy = false;
        updateForm();
      }
    }
  }

  question.addEventListener("input", updateForm);
  byId("question-form").addEventListener("submit", submit);
  byId("cancel").addEventListener("click", () =>
    state.active?.controller.abort(),
  );
  byId("source-filter").addEventListener("input", filterSources);
  doc
    .querySelectorAll("input[name=scope]")
    .forEach((input) => input.addEventListener("change", updateForm));
  byId("clear-selection").addEventListener("click", () => {
    state.selected.clear();
    message(byId("selection-error"), "");
    renderSources();
    updateForm();
  });
  byId("clear-chat").addEventListener("click", () => {
    state.active?.controller.abort();
    state.active = null;
    state.busy = false;
    byId("conversation").replaceChildren();
    byId("empty-state").hidden = false;
    byId("clear-chat").hidden = true;
    updateForm();
  });
  byId("retry-sources").addEventListener("click", refreshStatus);
  doc.querySelectorAll("[data-example]").forEach((button) =>
    button.addEventListener("click", () => {
      question.value = button.dataset.example;
      question.focus();
      updateForm();
    }),
  );
  const initialized = (async () => {
    try {
      const { body, ok } = await fetchJSON(fetcher, "/ui/config");
      if (
        !ok ||
        !Number.isInteger(body.question_max_characters) ||
        body.question_max_characters < 1 ||
        !Number.isInteger(body.selected_document_limit) ||
        body.selected_document_limit < 1 ||
        !Number.isFinite(body.request_timeout_seconds) ||
        body.request_timeout_seconds <= 0
      )
        throw new Error("Invalid configuration");
      state.config = body;
      state.configLoaded = true;
      question.maxLength = body.question_max_characters;
    } catch {
      message(
        byId("health-notice"),
        "Unable to load the chat. Please reload this page to try again.",
      );
      byId("availability").hidden = false;
      byId("retry-sources").hidden = true;
      return;
    }
    await refreshStatus();
  })();
  return { state, initialized };
}

if (typeof document !== "undefined") mount(document, window.fetch.bind(window));
