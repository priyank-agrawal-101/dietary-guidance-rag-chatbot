"""Explicit registry names/aliases and unavailable-selection handling; no scope fallback."""

import re
import unicodedata

from dietary_rag.corpus.identity import stable_id
from dietary_rag.retrieval.contracts import ScopeIssue


def normalized_name(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    return " ".join(re.findall(r"[^\W_]+", value)).removeprefix("the ")


class DocumentResolver:
    def __init__(self, registry, aliases):
        if aliases.get("version") != "document_aliases_v1" or set(aliases) != {
            "version",
            "aliases",
        }:
            raise ValueError("unsupported document alias manifest")
        self.registry, self.names = registry, {}
        for source in registry.sources:
            for name in (source.document_id, source.document_name, source.publisher):
                self.names.setdefault(normalized_name(name), set()).add(source.document_id)
        for name, identifiers in aliases["aliases"].items():
            if (
                not normalized_name(name)
                or not identifiers
                or len(set(identifiers)) != len(identifiers)
            ):
                raise ValueError("aliases require nonempty unique registry IDs")
            for identifier in identifiers:
                try:
                    registry.get(identifier)
                except KeyError as exc:
                    raise ValueError(f"alias references unknown document: {identifier}") from exc
            self.names.setdefault(normalized_name(name), set()).update(identifiers)
        self.configuration_id = stable_id(
            "document_resolution",
            {
                "normalization": "nfkc_casefold_and_alnum_v1",
                "aliases": aliases,
                "registry": registry.model_dump(mode="json"),
            },
        )

    def resolve(self, request, indexed_ids):
        question, issues = request.question, []
        names = list(request.document_names or ())
        if question.casefold().startswith("according to "):
            match = re.match(r"^according to\s+([^,:\n]+)[,:]\s*(.+)$", question, re.I | re.S)
            if not match:
                issues.append(
                    ScopeIssue(
                        kind="invalid_name_syntax",
                        selection=question,
                        reason="Use 'According to <document name>, <question>' or an explicit selection",
                    )
                )
            else:
                names.append(match[1].strip())
                question = match[2].strip()
        resolved = []
        for name in names:
            candidates = sorted(self.names.get(normalized_name(name), ()))
            if len(candidates) != 1:
                issues.append(
                    ScopeIssue(
                        kind="ambiguous" if candidates else "unknown",
                        selection=name,
                        candidate_document_ids=tuple(candidates),
                        reason="Name must resolve to exactly one reviewed registry ID",
                    )
                )
            else:
                resolved.append(candidates[0])
        explicit = request.selected_document_ids
        if explicit is not None and resolved and set(explicit) != set(resolved):
            issues.append(
                ScopeIssue(
                    kind="conflicting",
                    selection=", ".join(names),
                    candidate_document_ids=tuple(sorted(set(resolved))),
                    reason="Named scope contradicts explicit document IDs",
                )
            )
        selected = (
            tuple(sorted(set(explicit if explicit is not None else resolved)))
            if explicit is not None or names
            else None
        )
        for identifier in selected or ():
            try:
                eligibility = self.registry.consumer_eligibility(identifier)
            except KeyError:
                issues.append(
                    ScopeIssue(
                        kind="unknown", selection=identifier, reason="Unknown registry document ID"
                    )
                )
                continue
            if not eligibility.eligible_for_consumer_ingestion or identifier not in indexed_ids:
                issues.append(
                    ScopeIssue(
                        kind="unavailable",
                        selection=identifier,
                        candidate_document_ids=(identifier,),
                        reason=", ".join(eligibility.reasons)
                        or "No reviewed evidence in selected snapshot",
                    )
                )
        return question, selected, tuple(issues)
