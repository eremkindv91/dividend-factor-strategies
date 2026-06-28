from __future__ import annotations


IFRS_MARKERS = (
    "мсфо", "ifrs", "financial statements", "consolidated financial statements",
    "condensed financial statements",
    "консолидированная финансовая отчетность", "statement of financial position",
    "statement of profit or loss", "statement of cash flows",
    "отчет о финансовом положении", "отчет о прибыли или убытке",
    "отчет о движении денежных средств",
)


def classify_document(title: str = "", text_sample: str = "") -> dict:
    hay = f"{title}\n{text_sample}".lower()
    if "презентац" in hay or "presentation" in hay:
        return {"document_type": "presentation", "confidence": 0.85}
    if "рсбу" in hay or "rsbu" in hay or "ras" in hay:
        return {"document_type": "RAS", "confidence": 0.8}
    is_ifrs = any(m in hay for m in IFRS_MARKERS)
    annual_markers = (
        "год", "annual", "12 месяцев", "12 months", "year ended", "for the year",
        "31 december", "31 декабря", "fy ",
    )
    interim_markers = (
        "interim", "three months", "six months", "nine months", "3 months", "6 months", "9 months",
        "1q", "2q", "3q", "q1", "q2", "q3", "первый квартал", "девять месяцев", "шесть месяцев",
    )
    if is_ifrs and any(m in hay for m in interim_markers) and not any(m in hay for m in annual_markers):
        return {"document_type": "IFRS interim", "confidence": 0.82}
    if is_ifrs and any(m in hay for m in annual_markers):
        return {"document_type": "IFRS annual", "confidence": 0.85}
    if is_ifrs:
        return {"document_type": "IFRS interim", "confidence": 0.78}
    return {"document_type": "unknown", "confidence": 0.0}
