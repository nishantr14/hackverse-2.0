"""
Retrieval — BM25 over employee resume and preference chunks.
Owner: Livana (RAG integration).
Phase: Tier 3.

WHY BM25 AND NOT EMBEDDINGS. There is no vector store, no embedding model
and no inference service in this repository, and the brief rules out adding
one. BM25 is the standard sparse retrieval algorithm, it is about forty
lines, it needs no dependency beyond the standard library, and it is
deterministic — the same corpus and query give the same ranking and the same
scores every time, which matters more for a demo than semantic recall over
nine documents would.

It is genuine retrieval and not a lookup: nothing here knows the employees,
the projects or the skills. It scores whatever text it is given against
whatever query it is given, by term statistics over the corpus. Adding an
employee changes every other employee's IDF and therefore their scores. A
hardcoded ranking would not do that, and there is a test that checks it.

CHUNK-LEVEL, NOT DOCUMENT-LEVEL. Each project, each experience line, the
skills list and the stated preferences are separate documents. A candidate
should surface because one specific project matches, and the manager should
see that project rather than a whole resume with the relevant sentence
buried in it.

The known limitation is the one every lexical retriever has: it matches
words, not meanings. "Distributed Systems" will not retrieve a chunk that
only says "consensus protocol". For a nine-document corpus of technical
résumés, where the vocabulary is shared and specific, that trade is worth
making at this size and is stated rather than hidden.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.rag.store import Employee

#: Standard BM25 parameters. k1 controls how fast term frequency saturates,
#: b how strongly length normalisation applies. These are the usual defaults;
#: nothing here is tuned to make a particular employee win.
K1 = 1.5
B = 0.75

#: Words carrying no retrieval signal in this corpus. Deliberately short —
#: an aggressive list starts removing terms that matter ("no", in "no backend
#: experience", is the difference between a match and its opposite).
STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the
    to using with was were will their they this these those than then them""".split()
)

_TOKEN = re.compile(r"[a-z0-9+#.]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, stopwords dropped.

    Keeps `+`, `#` and `.` inside tokens so "C++", "C#" and "CI/CD" survive
    as recognisable terms rather than being split into noise.
    """
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


@dataclass(frozen=True)
class Document:
    """One retrievable chunk of evidence about one employee."""

    employee_id: str
    source: str  # 'resume' | 'preference'
    kind: str  # 'project' | 'experience' | 'skills' | 'preference'
    text: str


@dataclass(frozen=True)
class Hit:
    employee_id: str
    source: str
    kind: str
    text: str
    score: float


def _preference_sentence(e: Employee) -> str:
    """Stated preferences as prose, so they are retrievable by the same
    mechanism as a resume rather than needing a second matching path."""
    p = e.preferences
    parts: list[str] = []
    if p.preferred_shift:
        parts.append(f"Prefers {p.preferred_shift} shift work")
    if p.work_areas:
        parts.append("interested in " + ", ".join(p.work_areas))
    if p.availability:
        parts.append("available " + ", ".join(p.availability))
    if p.work_style:
        parts.append(f"works {p.work_style}")
    parts.append(
        "open to joining other teams" if p.open_to_other_teams else "prefers to stay on the current team"
    )
    return ". ".join(parts) + "."


def build_documents(employees: tuple[Employee, ...]) -> list[Document]:
    docs: list[Document] = []
    for e in employees:
        for project in e.resume.projects:
            docs.append(Document(e.employee_id, "resume", "project", project))
        for line in e.resume.experience:
            docs.append(Document(e.employee_id, "resume", "experience", line))
        if e.resume.skills:
            docs.append(
                Document(e.employee_id, "resume", "skills", "Skills: " + ", ".join(e.resume.skills))
            )
        docs.append(Document(e.employee_id, "preference", "preference", _preference_sentence(e)))
    return docs


class BM25Index:
    """A BM25 index over a fixed corpus. Built once, queried many times."""

    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents
        self._tokens = [tokenize(d.text) for d in documents]
        self._tf = [Counter(t) for t in self._tokens]
        self._len = [len(t) for t in self._tokens]
        self._avg_len = (sum(self._len) / len(self._len)) if self._len else 0.0

        df: Counter[str] = Counter()
        for toks in self._tokens:
            df.update(set(toks))
        n = len(documents)
        # Robertson/Sparck-Jones IDF with the +1 that keeps it non-negative:
        # a term in every document scores 0 rather than going negative and
        # penalising a document for containing it.
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def search(self, query: str, limit: int | None = None, min_score: float = 0.0) -> list[Hit]:
        """Score every document against the query, best first.

        Ties break on (employee_id, text) so the ordering is total and the
        output is byte-identical between runs.
        """
        q = tokenize(query)
        if not q or not self.documents:
            return []

        hits: list[Hit] = []
        for i, doc in enumerate(self.documents):
            tf, dl = self._tf[i], self._len[i]
            score = 0.0
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + K1 * (1 - B + B * (dl / self._avg_len if self._avg_len else 1))
                score += self._idf.get(term, 0.0) * (f * (K1 + 1)) / denom
            if score > min_score:
                hits.append(Hit(doc.employee_id, doc.source, doc.kind, doc.text, round(score, 4)))

        hits.sort(key=lambda h: (-h.score, h.employee_id, h.text))
        return hits[:limit] if limit else hits


def build_query(
    required_skills: list[str] | tuple[str, ...] = (),
    project: str | None = None,
    component: str | None = None,
    shift: str | None = None,
    availability: list[str] | tuple[str, ...] = (),
) -> str:
    """The retrieval query, assembled from the scenario.

    Plain concatenation on purpose: BM25 already weights rare terms above
    common ones, so repeating "Python" to emphasise it would be hand-tuning
    the ranking rather than describing the need.
    """
    parts = [*required_skills]
    if project:
        parts.append(project)
    if component:
        parts.append(component)
    if shift:
        parts.append(f"{shift} shift")
    parts.extend(availability)
    return " ".join(str(p) for p in parts if p)
