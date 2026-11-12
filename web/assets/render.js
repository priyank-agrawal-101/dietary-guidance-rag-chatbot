// API values are text, never HTML. Links must resolve to the document's reviewed URL.
export const ERROR_LABELS = {
  invalid_request: "Check your question or source selection",
  unknown_document: "Source not recognized",
  source_unavailable: "Selected source unavailable",
  corpus_unavailable: "Source collection unavailable",
  model_unavailable: "Answer service unavailable",
  answer_validation_failed: "Answer withheld",
  request_timeout: "Request timed out",
  usage_limit_exceeded: "Usage limit reached",
  token_budget_exceeded: "Try a narrower question",
  service_busy: "Please try again shortly",
};
const STATES = {
  answered: "Answered from sources",
  not_covered: "I couldn't find that in these sources",
  out_of_scope: "I can't help with that topic",
  requires_structured_data: "Nutrient lookup unavailable",
  needs_clarification: "Could you tell me a little more?",
};

export function element(doc, tag, text, className) {
  const node = doc.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function safeSourceURL(value, reviewedURL) {
  try {
    const link = new URL(value);
    const reviewed = new URL(reviewedURL);
    if (
      link.protocol !== "https:" ||
      reviewed.protocol !== "https:" ||
      link.username ||
      link.password ||
      reviewed.username ||
      reviewed.password ||
      link.origin !== reviewed.origin ||
      link.pathname !== reviewed.pathname ||
      link.search !== reviewed.search
    )
      return null;
    return link.href;
  } catch {
    return null;
  }
}

export function sourceLink(doc, label, value, reviewedURL) {
  const url = safeSourceURL(value, reviewedURL);
  if (!url) return element(doc, "span", `${label} unavailable`, "muted");
  const link = element(doc, "a", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.referrerPolicy = "no-referrer";
  return link;
}

function nonempty(value) {
  return typeof value === "string" && value.trim().length > 0;
}
function strings(value) {
  return Array.isArray(value) && value.every(nonempty);
}

// Structural checks guard malformed transport responses; factual validation stays on the server.
export function validOutcome(value, successful) {
  if (!value || !nonempty(value.request_id)) return false;
  if (!successful)
    return (
      Object.hasOwn(ERROR_LABELS, value.code) &&
      nonempty(value.message) &&
      !Object.hasOwn(value, "status") &&
      !Object.hasOwn(value, "document_answers") &&
      !Object.hasOwn(value, "citations")
    );
  if (!Object.hasOwn(STATES, value.status)) return false;
  if (value.status !== "answered") {
    return (
      nonempty(value.message) &&
      Array.isArray(value.document_answers) &&
      value.document_answers.length === 0 &&
      Array.isArray(value.citations) &&
      value.citations.length === 0
    );
  }
  if (
    !nonempty(value.corpus_version) ||
    !strings(value.searched_documents) ||
    !strings(value.unanswered_aspects) ||
    !Array.isArray(value.document_answers) ||
    !value.document_answers.length ||
    !Array.isArray(value.citations) ||
    !value.citations.length
  )
    return false;
  const citations = new Map();
  for (const citation of value.citations) {
    if (
      !citation ||
      !nonempty(citation.citation_id) ||
      citations.has(citation.citation_id) ||
      !nonempty(citation.document_id) ||
      !nonempty(citation.document_name) ||
      !nonempty(citation.publisher) ||
      !nonempty(citation.source_tier) ||
      !nonempty(citation.section_heading) ||
      !strings(citation.support_quotes) ||
      !citation.support_quotes.length ||
      !["html", "pdf"].includes(citation.format) ||
      !Array.isArray(citation.physical_pages) ||
      !Array.isArray(citation.page_urls) ||
      !Array.isArray(citation.printed_page_labels)
    )
      return false;
    if (
      citation.format === "pdf" &&
      (!citation.physical_pages.length ||
        !citation.physical_pages.every(
          (page) => Number.isInteger(page) && page > 0,
        ))
    )
      return false;
    if (
      citation.page_urls.length &&
      citation.page_urls.length !== citation.physical_pages.length
    )
      return false;
    citations.set(citation.citation_id, citation);
  }
  const docs = new Set();
  const used = new Set();
  for (const answer of value.document_answers) {
    if (
      !answer ||
      !nonempty(answer.document_id) ||
      docs.has(answer.document_id) ||
      !value.searched_documents.includes(answer.document_id) ||
      !Array.isArray(answer.claims) ||
      !answer.claims.length
    )
      return false;
    docs.add(answer.document_id);
    for (const claim of answer.claims) {
      if (
        !claim ||
        !nonempty(claim.text) ||
        !strings(claim.citation_ids) ||
        !claim.citation_ids.length
      )
        return false;
      for (const id of claim.citation_ids) {
        if (citations.get(id)?.document_id !== answer.document_id) return false;
        used.add(id);
      }
    }
  }
  return used.size === citations.size;
}

function citationNode(doc, citation, documents) {
  const box = element(doc, "div", undefined, "citation");
  box.append(element(doc, "p", citation.document_name, "citation-heading"));
  const date =
    citation.date_label ||
    (citation.publication_year
      ? String(citation.publication_year)
      : "Publication year not recorded");
  box.append(
    element(
      doc,
      "p",
      [
        citation.publisher,
        citation.jurisdiction,
        citation.source_tier.replaceAll("_", " "),
        date,
      ]
        .filter(Boolean)
        .join(" · "),
      "citation-location",
    ),
  );
  box.append(element(doc, "p", citation.section_heading, "citation-location"));
  if (citation.physical_pages.length)
    box.append(
      element(
        doc,
        "p",
        `Physical PDF pages: ${citation.physical_pages.join(", ")}`,
        "citation-location",
      ),
    );
  if (citation.printed_page_labels.length)
    box.append(
      element(
        doc,
        "p",
        `Printed page labels: ${citation.printed_page_labels.join(", ")}`,
        "citation-location",
      ),
    );
  const reviewed = documents.get(citation.document_id)?.source_url;
  const links = element(doc, "div", undefined, "citation-links");
  links.append(
    sourceLink(doc, "Open source ↗", citation.source_url, reviewed),
  );
  citation.page_urls.forEach((url, index) =>
    links.append(
      sourceLink(
        doc,
        `PDF page ${citation.physical_pages[index]} ↗`,
        url,
        reviewed,
      ),
    ),
  );
  box.append(links);
  const details = element(doc, "details");
  details.append(element(doc, "summary", "Read supporting excerpts"));
  citation.support_quotes.forEach((quote) =>
    details.append(element(doc, "blockquote", quote)),
  );
  box.append(details);
  return box;
}

export function renderOutcome(doc, body, { documents, scope }) {
  const container = element(doc, "div");
  const heading = element(doc, "div", undefined, "result-heading");
  const label = body.code
    ? ERROR_LABELS[body.code]
    : body.status === "answered" && body.unanswered_aspects.length
      ? "Partly supported by sources"
      : STATES[body.status];
  heading.append(element(doc, "h3", label));
  if (body.status === "answered")
    heading.append(
      element(
        doc,
        "span",
        body.unanswered_aspects.length ? "Partial answer" : "Cited answer",
        "badge good",
      ),
    );
  container.append(heading);
  if (body.status === "answered") {
    const citations = new Map(
      body.citations.map((citation) => [citation.citation_id, citation]),
    );
    const answers = new Map(
      body.document_answers.map((answer) => [answer.document_id, answer]),
    );
    const ids =
      scope.mode === "selected" && scope.document_ids.length > 1
        ? body.searched_documents
        : [...answers.keys()];
    const grid = element(
      doc,
      "div",
      undefined,
      ids.length > 1 ? "answer-grid comparison" : "answer-grid",
    );
    for (const id of ids) {
      const source = documents.get(id);
      const answer = answers.get(id);
      const citation = answer
        ? citations.get(answer.claims[0].citation_ids[0])
        : null;
      const card = element(doc, "article", undefined, "source-answer");
      card.dataset.documentId = id;
      card.append(
        element(
          doc,
          "h4",
          source?.document_name || citation?.document_name || id,
        ),
      );
      if (source)
        card.append(
          element(
            doc,
            "p",
            [source.publisher, source.format.toUpperCase(), source.jurisdiction]
              .filter(Boolean)
              .join(" · "),
            "source-meta",
          ),
        );
      if (!answer) {
        card.append(element(doc, "span", "Evidence gap", "badge warning"));
        card.append(
          element(
            doc,
            "p",
            "No supported answer was returned from this document. See the evidence gaps below.",
            "muted",
          ),
        );
      } else {
        for (const claim of answer.claims) {
          const section = element(doc, "div", undefined, "claim");
          section.append(element(doc, "p", claim.text, "claim-text"));
          claim.citation_ids.forEach((key) =>
            section.append(citationNode(doc, citations.get(key), documents)),
          );
          card.append(section);
        }
      }
      grid.append(card);
    }
    container.append(grid);
    if (body.unanswered_aspects.length) {
      const gaps = element(doc, "section", undefined, "notice");
      gaps.append(
        element(
          doc,
          "p",
          "Some parts of your question or selected sources aren't covered by this answer. Try narrowing the question or choosing a different source.",
        ),
      );
      container.append(gaps);
    }
  } else {
    const notice = element(
      doc,
      "div",
      undefined,
      body.code ? "notice error" : "notice",
    );
    const message =
      body.status === "not_covered"
        ? "I couldn't find a supported answer in the sources searched. Try a more specific food question or choose different sources."
        : body.code === "service_busy"
          ? "I can't take another question just yet. Please try again in a moment."
          : body.code === "token_budget_exceeded"
            ? "Choose fewer sources or ask a narrower question, then try again."
            : body.code === "model_unavailable"
              ? "Answers are temporarily unavailable. Please try again later."
              : body.message;
    notice.append(element(doc, "p", message));
    if (
      Number.isInteger(body.retry_after_seconds) &&
      body.retry_after_seconds > 0
    ) {
      notice.append(
        element(
          doc,
          "p",
          `Try again in about ${body.retry_after_seconds} seconds.`,
          "fine-print",
        ),
      );
    }
    container.append(notice);
  }
  if (body.searched_documents?.length) {
    const names = body.searched_documents.map(
      (id) => documents.get(id)?.document_name || id,
    );
    const searched = element(doc, "details", undefined, "request-details");
    searched.append(
      element(
        doc,
        "summary",
        `Searched ${names.length} source${names.length === 1 ? "" : "s"}`,
      ),
    );
    const list = element(doc, "ul");
    names.forEach((name) => list.append(element(doc, "li", name)));
    searched.append(list);
    container.append(searched);
  }
  const diagnostics = element(doc, "details", undefined, "request-details");
  diagnostics.append(element(doc, "summary", "Diagnostic details"));
  diagnostics.append(
    element(
      doc,
      "pre",
      JSON.stringify(
        {
          request_id: body.request_id,
          status: body.status,
          error_code: body.code,
          error_message: body.code ? body.message : undefined,
          corpus_version: body.corpus_version,
          searched_document_ids: body.searched_documents,
          unanswered_aspects: body.unanswered_aspects,
        },
        null,
        2,
      ),
      "diagnostic-json",
    ),
  );
  container.append(diagnostics);
  return container;
}
