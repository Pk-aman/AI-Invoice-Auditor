"""Translation agent: language detection, translation to English, composite confidence."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app import prompts
from app.core.llm import LLM
from app.extraction.language import detect_language

_NUM = re.compile(r"\d[\d.,]*\d|\d")
MAX_CHUNK_CHARS = 1500
SAMPLE_CHUNKS = 2


@dataclass
class TranslationResult:
    text: str
    language: str
    confidence: float
    translated: bool


def chunk_paragraphs(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for para in text.splitlines(keepends=True):
        while len(para) > limit:  # one very long line
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(para[:cut])
            para = para[cut:]
        if len(cur) + len(para) > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += para
    if cur.strip():
        chunks.append(cur)
    return chunks


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in _NUM.findall(text)}


def number_loss_ratio(source: str, translated: str) -> float:
    src, dst = _numbers(source), _numbers(translated)
    return 0.0 if not src else len(src - dst) / len(src)


def _similarity(a: str, b: str) -> float:
    na = re.sub(r"\W+", " ", a.lower()).strip()
    nb = re.sub(r"\W+", " ", b.lower()).strip()
    return difflib.SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0


def translate_text(raw_text: str, llm: LLM, *, hint_language: str | None = None) -> TranslationResult:
    lang, prob = detect_language(raw_text)
    if hint_language and lang == "unknown":
        lang, prob = hint_language, 0.6
    if lang == "en" and prob >= 0.9:
        return TranslationResult(raw_text, "en", round(prob, 3), False)

    cfg = prompts.load("translation")
    system = prompts.persona() + "\n\n" + cfg["translate"]["system"]
    chunks = chunk_paragraphs(raw_text)
    out: list[str] = []
    for ch in chunks:
        user = prompts.render(cfg["translate"]["user"], source_language=lang, text=ch)
        out.append(llm.complete("translation", system, user).strip("\n"))
    translated = "\n".join(out)

    # confidence = 0.5 * language-id probability + 0.5 * round-trip similarity on a sample;
    # self-reported model confidence is deliberately not trusted
    sims: list[float] = []
    if lang not in ("en", "unknown"):
        bt = cfg["back_translate"]
        for src_chunk, tr_chunk in list(zip(chunks, out, strict=True))[:SAMPLE_CHUNKS]:
            back = llm.complete("translation", bt["system"], prompts.render(bt["user"], target_language=lang, text=tr_chunk))
            sims.append(_similarity(src_chunk, back))
    round_trip = sum(sims) / len(sims) if sims else 0.5
    confidence = 0.5 * prob + 0.5 * round_trip
    loss = number_loss_ratio(raw_text, translated)
    if loss > 0.1:  # identifiers/amounts must survive translation
        confidence = min(confidence, 0.7 * (1 - loss))
    return TranslationResult(translated, lang, round(max(0.0, min(1.0, confidence)), 3), True)
