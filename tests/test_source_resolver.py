from src.unification.source_resolver import SourceCandidate, resolve_best_value


def test_manual_override_wins():
    resolved = resolve_best_value([
        SourceCandidate(100, "smartlab", "aggregator", quality_score=70, confidence_score=0.7, source_url="https://smart-lab.ru/q/TEST/f/y/"),
        SourceCandidate(110, "manual_override", "manual_override", quality_score=100, confidence_score=1),
    ])

    assert resolved["best"].value == 110
    assert resolved["selected_reason"] == "manual_override"
    assert resolved["needs_manual_review"] is False


def test_official_large_conflict_keeps_smartlab_for_review():
    resolved = resolve_best_value([
        SourceCandidate(100, "smartlab", "aggregator", quality_score=70, confidence_score=0.7, source_url="https://smart-lab.ru/q/TEST/f/y/"),
        SourceCandidate(120, "official_ifrs_pdf_table", "official_ifrs", quality_score=90, confidence_score=0.9, source_url="https://issuer.example/report.pdf"),
    ])

    assert resolved["best"].source_name == "smartlab"
    assert resolved["conflict_flag"] is True
    assert resolved["needs_manual_review"] is True
    assert resolved["conflict_type"] == "value_conflict_gt_5pct"


def test_low_confidence_ocr_does_not_override_smartlab():
    resolved = resolve_best_value([
        SourceCandidate(100, "smartlab", "aggregator", quality_score=70, confidence_score=0.7, source_url="https://smart-lab.ru/q/TEST/f/y/"),
        SourceCandidate(101, "official_ifrs_ocr_low_confidence", "official_ifrs", quality_score=85, confidence_score=0.6, source_url="https://issuer.example/report.pdf", extraction_method="ocr"),
    ])

    assert resolved["best"].source_name == "smartlab"
    assert resolved["selected_reason"] == "ocr_low_confidence_keep_smartlab"
