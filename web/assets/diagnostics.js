import { fetchJSON } from "./client.js";
import { element } from "./render.js";

export function mountDiagnostics(doc, fetcher) {
  const tabs = [...doc.querySelectorAll("[data-tab]")];
  const loaded = new Set();
  const pending = new Map();
  const format = (value) => Number(value).toLocaleString("en-US");
  let timeout = 65000;
  function raw(body) {
    return element(
      doc,
      "pre",
      JSON.stringify(body, null, 2),
      "diagnostic-json",
    );
  }
  async function refresh(name) {
    if (pending.has(name)) return pending.get(name);
    const content = doc.getElementById(`${name}-content`);
    const button = doc.querySelector(`[data-refresh="${name}"]`);
    button.disabled = true;
    content.replaceChildren(element(doc, "p", "Loading…", "muted"));
    const task = (async () => {
      try {
        if (name === "status") {
          const config = await fetchJSON(fetcher, "/ui/config");
          if (
            config.ok &&
            Number.isFinite(config.body.request_timeout_seconds) &&
            config.body.request_timeout_seconds > 0
          )
            timeout = (config.body.request_timeout_seconds + 5) * 1000;
          const live = await fetchJSON(fetcher, "/health/live");
          const ready = await fetchJSON(fetcher, "/health/ready", { timeout });
          content.replaceChildren(
            element(
              doc,
              "h3",
              live.ok && live.body.status === "alive"
                ? "Server is running"
                : "Server is unavailable",
            ),
            element(
              doc,
              "p",
              ready.ok && ready.body.status === "ready"
                ? "Sources and answer service are ready."
                : "The answer service is not ready.",
            ),
            element(doc, "h3", "Readiness response"),
            raw(ready.body),
            element(doc, "h3", "Interface limits"),
            raw(config.body),
          );
        } else if (name === "sources") {
          const { body } = await fetchJSON(fetcher, "/documents", { timeout });
          if (
            !Array.isArray(body.documents) ||
            body.documents.some(
              (source) =>
                !source ||
                typeof source.document_name !== "string" ||
                typeof source.available !== "boolean",
            )
          )
            throw new Error();
          const available = body.documents.filter(
            (source) => source.available,
          ).length;
          const list = element(doc, "ul", undefined, "diagnostic-sources");
          for (const source of body.documents) {
            const row = element(doc, "li");
            row.append(
              element(doc, "strong", source.document_name),
              element(
                doc,
                "p",
                `${source.available ? "Available" : "Unavailable"} · ${source.publisher || "Unknown publisher"} · ${source.document_id}`,
              ),
            );
            list.append(row);
          }
          content.replaceChildren(
            element(
              doc,
              "p",
              `${available} available · ${body.documents.length - available} unavailable`,
            ),
            element(
              doc,
              "p",
              `Corpus snapshot: ${body.corpus_version || "Unavailable"}`,
              "fine-print",
            ),
            list,
          );
          if (body.error) content.append(raw(body.error));
        } else if (name === "usage") {
          const { body, ok } = await fetchJSON(fetcher, "/usage");
          if (!ok) throw new Error();
          if (body.generation === "offline" && body.usage === null) {
            content.replaceChildren(
              element(
                doc,
                "p",
                "Offline generation is active. No Groq calls or token usage are recorded.",
              ),
            );
            return;
          }
          const rows = [];
          for (const [name, label] of [
            ["minute", "Past minute"],
            ["hour", "Past hour"],
            ["day", "Past 24 hours"],
          ]) {
            const window = body.windows?.[name];
            if (
              !window ||
              ![
                "requests",
                "tokens",
                "request_limit",
                "token_limit",
                "pending_calls",
              ].every((key) => Number.isFinite(window[key]) && window[key] >= 0)
            )
              throw new Error();
            const row = element(doc, "div", undefined, "usage-row");
            row.append(
              element(doc, "h3", label),
              element(
                doc,
                "p",
                `${format(window.requests)} / ${format(window.request_limit)} calls · ${format(window.tokens)} / ${format(window.token_limit)} tokens · ${format(window.pending_calls)} pending`,
              ),
            );
            const meter = element(doc, "meter");
            meter.min = 0;
            meter.max = Math.max(1, window.token_limit);
            meter.value = window.tokens;
            meter.setAttribute("aria-label", `${label} token usage`);
            row.append(meter);
            rows.push(row);
          }
          content.replaceChildren(...rows);
          if (body.retry_after_seconds > 0)
            content.append(
              element(
                doc,
                "p",
                `Provider cooldown: about ${Math.ceil(body.retry_after_seconds)} seconds remaining.`,
              ),
            );
        }
      } catch {
        content.replaceChildren(
          element(
            doc,
            "p",
            "This check is unavailable. Refresh to try again.",
            "notice error",
          ),
        );
      } finally {
        button.disabled = false;
        pending.delete(name);
      }
    })();
    pending.set(name, task);
    return task;
  }
  function activate(tab, focus = false) {
    for (const candidate of tabs) {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      doc.getElementById(candidate.getAttribute("aria-controls")).hidden =
        !selected;
    }
    if (focus) tab.focus();
    const name = tab.dataset.tab;
    if (name !== "api" && !loaded.has(name)) {
      loaded.add(name);
      return refresh(name);
    }
    return Promise.resolve();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      let target;
      if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft")
        target = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") target = 0;
      else if (event.key === "End") target = tabs.length - 1;
      else return;
      event.preventDefault();
      activate(tabs[target], true);
    });
  });
  doc
    .querySelectorAll("[data-refresh]")
    .forEach((button) =>
      button.addEventListener("click", () => refresh(button.dataset.refresh)),
    );
  return { initialized: activate(tabs[0]) };
}

if (typeof document !== "undefined")
  mountDiagnostics(document, window.fetch.bind(window));
