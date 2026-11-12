"""Deterministic in-memory BM25; scoped statistics, no persistent lexical index."""

import math
import re
from collections import Counter


def terms(text):
    return re.findall(r"[a-z0-9]+", text.casefold())


class BM25:
    def __init__(self, texts, *, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.frequencies = {identifier: Counter(terms(text)) for identifier, text in texts.items()}
        self.lengths = {identifier: sum(f.values()) for identifier, f in self.frequencies.items()}
        self.average = sum(self.lengths.values()) / len(texts) if texts else 0
        self.df = Counter(t for f in self.frequencies.values() for t in f)

    def rank(self, question, *, include_zero=False):
        query = set(terms(question))
        scores = []
        for identifier, frequencies in self.frequencies.items():
            score = sum(
                math.log(1 + (len(self.frequencies) - self.df[t] + 0.5) / (self.df[t] + 0.5))
                * frequencies[t]
                * (self.k1 + 1)
                / (
                    frequencies[t]
                    + self.k1 * (1 - self.b + self.b * self.lengths[identifier] / self.average)
                )
                for t in query
                if frequencies[t]
            )
            if score > 0 or include_zero:
                scores.append((identifier, score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))
