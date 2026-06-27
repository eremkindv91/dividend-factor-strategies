from __future__ import annotations


IFRS_MARKERS = (
    "мсфо", "ifrs", "consolidated financial statements",
    "консолидированная финансовая отчетность", "statement of financial position",
    "statement of profit or loss", "statement of cash flows",
    "отчет о финансовом положении", "отчет о прибыли или убытке",
    "отчет о движении денежных средств",
)


def classify_document(title: str = "", text_sample: str = "") -> dict:
    hay = f"{title}\n{text_sample}".lower()
    is_ifrs = any(m in hay for m in IFRS_MARKERS)
    if is_ifrs and ("год" in hay or "annual" in hay or "12 месяцев" in hay):
        return {"document_type": "IFRS annual", "confidence": 0.85}
    if is_ifrs:
        return {"document_type": "IFRS interim", "confidence": 0.78}
    if "рсбу" in hay or "ras" in hay:
        return {"document_type": "RAS quarterly", "confidence": 0.8}
    if "презентац" in hay or "presentation" in hay:
        return {"document_type": "presentation", "confidence": 0.85}
    return {"document_type": "unknown", "confidence": 0.0}

