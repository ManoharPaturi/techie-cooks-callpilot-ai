"""Explicitly imported local notes, split into citable paragraphs, with simple lexical retrieval."""
from __future__ import annotations

import math
import re
from collections import Counter

from .models import NoteParagraph

MAX_NOTE_BYTES = 200_000
_WORD = re.compile(r"[a-z0-9₹]+")
STOPWORDS = set(
    "a an and are as at be by can could do does for from has have how i if in is it its me my of on or our "
    "so that the their them then there this to us was we what when where which will with you your yes no "
    "hi hello also just about please would should any".split()
)


def tokens(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in STOPWORDS or len(w) < 2:
            continue
        # crude stemming so "images"/"image", "delivered"/"delivery" overlap
        for suffix in ("ing", "ed", "es", "s", "y"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


def split_paragraphs(text: str) -> list[str]:
    paras: list[str] = []
    heading = ""
    for block in re.split(r"\n\s*\n", text.strip()):
        block = " ".join(block.split())
        if not block:
            continue
        if block.startswith("#") and len(block) < 120:
            heading = block.lstrip("# ").strip()
            continue
        paras.append(f"{heading}: {block}" if heading and not paras else block)
    return paras


class NoteStore:
    def __init__(self) -> None:
        self.paragraphs: dict[str, NoteParagraph] = {}
        self.files: list[str] = []
        self._doc_freq: Counter[str] = Counter()

    def import_text(self, filename: str, text: str) -> list[NoteParagraph]:
        """Importing a file with an already-imported name replaces it (keeps its note number)."""
        if filename in self.files:
            note_no = self.files.index(filename) + 1
            prefix = f"n-{note_no:03d}:"
            self.paragraphs = {k: v for k, v in self.paragraphs.items() if not k.startswith(prefix)}
            self._doc_freq = Counter()
            for p in self.paragraphs.values():
                self._doc_freq.update(set(tokens(p.content)))
        else:
            self.files.append(filename)
            note_no = len(self.files)
        added = []
        for i, content in enumerate(split_paragraphs(text), start=1):
            p = NoteParagraph(id=f"n-{note_no:03d}:p-{i:03d}", filename=filename, content=content)
            self.paragraphs[p.id] = p
            self._doc_freq.update(set(tokens(content)))
            added.append(p)
        return added

    def clear(self) -> None:
        self.__init__()

    def search(self, query: str, k: int = 3) -> list[NoteParagraph]:
        q = set(tokens(query))
        if not q:
            return []
        n = max(len(self.paragraphs), 1)
        scored = []
        for p in self.paragraphs.values():
            pt = set(tokens(p.content))
            score = sum(math.log(1 + n / self._doc_freq[t]) for t in q & pt)
            if score > 0:
                scored.append((score, p.id, p))
        scored.sort(key=lambda s: (-s[0], s[1]))
        if not scored:
            return []
        # Drop weak matches (e.g. a single shared common word) relative to the best hit.
        best = scored[0][0]
        return [p for score, _, p in scored[:k] if score >= 0.5 * best]
