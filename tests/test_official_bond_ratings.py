import json
from io import BytesIO

from openpyxl import Workbook

from bonds import official_ratings


def test_normalize_national_rating_scales():
    assert official_ratings.normalize_rating("A-(RU)") == "A-"
    assert official_ratings.normalize_rating("ruBBB+") == "BBB+"
    assert official_ratings.normalize_rating("AA.ru") == "AA"
    assert official_ratings.normalize_rating("A-|ru|") == "A-"
    assert official_ratings.normalize_rating("ruАА-") == "AA-"
    assert official_ratings.normalize_rating("рейтинг отозван") is None


def test_parse_acra_payload_keeps_exact_issue_isin_and_source():
    payload = {
        "success": True,
        "data": {
            "items": [
                {
                    "info": {
                        "rate": {"value": {"title": "A-(RU)", "is_withdrawn": False}},
                        "emission": {
                            "value": {
                                "isin": "RU000A107RZ0",
                                "url": "/ratings/emissions/1107/",
                            }
                        },
                        "pressRelease": {
                            "value": {
                                "timestamp": 1772442309,
                                "url": "/press-releases/6637/",
                            }
                        },
                    }
                }
            ]
        },
    }

    rows = official_ratings.parse_acra_payload(payload)

    assert rows == [
        {
            "isin": "RU000A107RZ0",
            "rating": "A-",
            "raw_rating": "A-(RU)",
            "rating_agency": "АКРА",
            "rating_scope": "issue",
            "rating_date": "2026-03-02",
            "rating_source_url": "https://www.acra-ratings.ru/ratings/emissions/1107/",
            "rating_press_release_url": "https://www.acra-ratings.ru/press-releases/6637/",
        }
    ]


def test_parse_nkr_official_workbook_ignores_withdrawn_rating():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "ID",
            "Issuer Name",
            "Rating Date",
            "Rating",
            "ISIN",
            "Security name",
            "Maturity",
            "Issuer TIN",
            "Issue volume",
            "Currency",
            "Issue date",
            "Press release",
        ]
    )
    sheet.append(
        [
            42,
            "Эмитент",
            "09.07.2026",
            "BBB+.ru",
            "RU000A10TEST",
            "Выпуск",
            None,
            "7700000000",
            None,
            "рублей",
            None,
            "ratings.ru/ratings/press-releases/test/",
        ]
    )
    sheet.append([43, "Эмитент", "09.07.2026", "рейтинг отозван", "RU000A10OLD0"])
    stream = BytesIO()
    workbook.save(stream)

    rows = official_ratings.parse_nkr_workbook(stream.getvalue())

    assert len(rows) == 1
    assert rows[0]["rating"] == "BBB+"
    assert rows[0]["rating_source_url"] == "https://ratings.ru/ratings/issues/42/"


def test_parse_expert_ra_page_and_isin_map():
    rating_html = """
    <table><tr><td><a href="/database/securities/bonds/1000063149">Эталон</a></td>
    <td>ruBBB+</td><td>&#8212;</td><td><a href="/releases/2026/apr17i">17.04.2026</a></td></tr></table>
    """
    mapping_html = """
    <a href="/database/securities/bonds/1000063149" class="ratinglist-table-link">RU000A10BAP4</a>
    """

    rows = official_ratings.parse_expert_rating_page(rating_html)
    mapping = official_ratings.parse_expert_isin_map(mapping_html)

    assert rows[0]["raw_rating"] == "ruBBB+"
    assert rows[0]["rating_date"] == "2026-04-17"
    assert mapping[rows[0]["detail_path"]] == "RU000A10BAP4"


def test_parse_expert_ra_exact_issue_card_uses_current_row():
    detail_html = """
    <meta name="description" content="Рейтинг: Кредитные рейтинги долговых инструментов - ruBBB+">
    <table class="object-rating-table">
      <thead><tr><th>Национальная шкала</th><th>Дата</th></tr></thead>
      <tr><td><span>ruBBB+</span></td><td><a href="/releases/2026/apr17i">17.04.2026</a></td></tr>
      <tr><td><span>ruA-</span></td><td><a href="/releases/2025/apr28g">28.04.2025</a></td></tr>
    </table>
    """

    row = official_ratings.parse_expert_detail_rating(
        detail_html, "RU000A10BAP4", "/database/securities/bonds/1000063149"
    )

    assert row == {
        "isin": "RU000A10BAP4",
        "rating": "BBB+",
        "raw_rating": "ruBBB+",
        "rating_agency": "Эксперт РА",
        "rating_scope": "issue",
        "rating_date": "2026-04-17",
        "rating_source_url": "https://raexpert.ru/database/securities/bonds/1000063149/",
        "rating_press_release_url": "https://raexpert.ru/releases/2026/apr17i",
    }


def test_expert_ra_exact_issue_card_rejects_stale_history():
    detail_html = """
    <meta name="description" content="Рейтинг отозван">
    <table class="object-rating-table">
      <tr><th>Национальная шкала</th><th>Дата</th></tr>
      <tr><td><span>ruBBB+</span></td><td>01.01.2024</td></tr>
    </table>
    """

    assert official_ratings.parse_expert_detail_rating(
        detail_html, "RU000A10BAP4", "/database/securities/bonds/1000063149"
    ) is None


def test_select_ratings_uses_worst_current_official_issue_rating():
    records = [
        {
            "isin": "RU000A10TEST",
            "rating": "A",
            "raw_rating": "A(RU)",
            "rating_agency": "АКРА",
            "rating_scope": "issue",
            "rating_date": "2026-06-01",
            "rating_source_url": "https://www.acra-ratings.ru/ratings/emissions/1/",
        },
        {
            "isin": "RU000A10TEST",
            "rating": "A-",
            "raw_rating": "ruA-",
            "rating_agency": "Эксперт РА",
            "rating_scope": "issue",
            "rating_date": "2026-07-01",
            "rating_source_url": "https://raexpert.ru/database/securities/bonds/1/",
        },
    ]

    selected = official_ratings.select_ratings(records, checked_at="2026-07-10T00:00:00+00:00")

    assert selected["RU000A10TEST"]["rating"] == "A-"
    assert selected["RU000A10TEST"]["rating_agency"] == "Эксперт РА"
    assert len(selected["RU000A10TEST"]["rating_records"]) == 2


def test_source_failure_is_isolated_without_surrogate_fallback():
    good = {
        "isin": "RU000A10TEST",
        "rating": "A-",
        "raw_rating": "A-(RU)",
        "rating_agency": "АКРА",
        "rating_scope": "issue",
        "rating_date": "2026-07-01",
        "rating_source_url": "https://www.acra-ratings.ru/ratings/emissions/1/",
    }

    def failed():
        raise TimeoutError("source timeout")

    selected, meta = official_ratings.load_official_ratings(
        ["RU000A10TEST"], loaders={"acra": lambda: [good], "expert_ra": failed}
    )

    assert selected["RU000A10TEST"]["rating"] == "A-"
    assert meta["sources_ok"] == 1
    assert meta["sources"]["expert_ra"]["status"] == "error"
    assert "issuer_map" not in json.dumps({"selected": selected, "meta": meta})
