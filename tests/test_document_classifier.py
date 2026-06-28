from src.extraction.document_classifier import classify_document


def test_classifies_ifrs_annual_report():
    res = classify_document("Годовой отчет МСФО", "консолидированная финансовая отчетность за год")

    assert res["document_type"] == "IFRS annual"
    assert res["confidence"] >= 0.8


def test_classifies_ras_separately_from_ifrs():
    res = classify_document("Бухгалтерская отчетность РСБУ", "")

    assert res["document_type"] == "RAS"


def test_classifies_english_annual_and_interim_ifrs_reports():
    annual = classify_document("Consolidated Financial Statements for the year ended 31 December 2025", "")
    interim = classify_document("Consolidated interim condensed financial statements for the six months ended 30 June 2025", "")

    assert annual["document_type"] == "IFRS annual"
    assert interim["document_type"] == "IFRS interim"
