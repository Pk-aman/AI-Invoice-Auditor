"""Language identification (langdetect, deterministic seed)."""
from __future__ import annotations

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

DetectorFactory.seed = 0


def detect_language(text: str) -> tuple[str, float]:
    """Return (iso-639-1 code, probability). ('unknown', 0.0) when undetectable."""
    sample = " ".join(text.split())[:3000]
    if len(sample) < 20:
        return "unknown", 0.0
    try:
        best = detect_langs(sample)[0]
    except LangDetectException:
        return "unknown", 0.0
    return best.lang.split("-")[0], float(best.prob)
