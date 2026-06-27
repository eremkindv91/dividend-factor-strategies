from src.extraction.document_classifier import classify_document


def test_classifies_ifrs_annual_report():
    res = classify_document("Годовой отчет МСФО", "консолидированная финансовая отчетность за год")

    assert res["document_type"] == "IFRS annual"
    assert res["confidence"] >= 0.8


def test_classifies_ras_separately_from_ifrs():
    res = classify_document("Бухгалтерская отчетность РСБУ", "")

    assert res["document_type"] == "RAS quarterly"
