from bonds import bond_finder


def test_rating_meets_profile_rejects_unrated_when_profile_disallows_it():
    profile = {"min_rating": "A-", "allow_unrated": False}

    assert not bond_finder.rating_meets_profile({"rating_rank": None}, profile)


def test_rating_meets_profile_applies_floor_and_allows_unrated_when_configured():
    profile = {"min_rating": "BBB-", "allow_unrated": True}

    assert bond_finder.rating_meets_profile({"rating_rank": None}, profile)
    assert bond_finder.rating_meets_profile({"rating_rank": bond_finder.RATING_RANK["BBB-"]}, profile)
    assert not bond_finder.rating_meets_profile({"rating_rank": bond_finder.RATING_RANK["BB+"]}, profile)


def test_attach_rating_never_infers_from_issuer_name():
    rec = {"secid": "RU000A10NONE", "name": "Сбер выпуск", "issuer": "Сбербанк"}

    bond_finder.attach_rating(rec, {})

    assert rec.get("rating") is None
    assert rec["rating_rank"] is None


def test_score_all_keeps_unrated_neutral_in_rating_zscore():
    cfg = {
        "score_weights": {
            "z_spread": 0.0,
            "risk_proxy": 0.0,
            "z_volume": 0.0,
            "cheapening_bonus": 0.0,
            "z_rating": 1.0,
        },
        "risk_proxy": {
            "listlevel": {"1": 0.0, "missing": 0.0},
            "qualified_only_penalty": 0.0,
            "issue_below_1bn": 0.0,
            "issue_above_3bn": 0.0,
        },
        "duration_buckets_years": [1, 2],
        "cheapening": {"bonus": 0.0, "bonus_percentile": 80},
    }
    cands = [
        {
            "secid": "AAA",
            "rating_rank": 10,
            "listlevel": 1,
            "issue_rub": 3_000_000_000,
            "duration_years": 1.0,
            "g_spread": 1.0,
            "median_vol": 1_000_000,
            "new_placement": False,
        },
        {
            "secid": "AA",
            "rating_rank": 7,
            "listlevel": 1,
            "issue_rub": 3_000_000_000,
            "duration_years": 1.0,
            "g_spread": 1.0,
            "median_vol": 1_000_000,
            "new_placement": False,
        },
        {
            "secid": "BBB",
            "rating_rank": 1,
            "listlevel": 1,
            "issue_rub": 3_000_000_000,
            "duration_years": 1.0,
            "g_spread": 1.0,
            "median_vol": 1_000_000,
            "new_placement": False,
        },
        {
            "secid": "NONE",
            "rating_rank": None,
            "listlevel": 1,
            "issue_rub": 3_000_000_000,
            "duration_years": 1.0,
            "g_spread": 1.0,
            "median_vol": 1_000_000,
            "new_placement": False,
        },
    ]

    bond_finder.score_all(cands, cfg)

    by_id = {row["secid"]: row for row in cands}
    assert by_id["AAA"]["score"] > 0
    assert by_id["BBB"]["score"] < 0
    assert by_id["NONE"]["score"] == 0


def _allocation_cfg(min_issuers=2):
    return {
        "portfolio": {
            "cap_per_bond": 0.6,
            "cap_per_issuer": 0.7,
            "min_issuers_meaningful": min_issuers,
        }
    }


def test_allocate_marks_portfolio_unconstructible_below_minimum_issuers():
    picks = [
        {"score": 2.0, "inn": "7701"},
        {"score": 1.0, "inn": "7701"},
    ]

    result = bond_finder.allocate(picks, _allocation_cfg(min_issuers=2))

    assert result["issuers"] == 1
    assert result["constructible"] is False
    assert result["min_issuers_required"] == 2
    assert "портфель не сформирован" in result["note"]


def test_allocate_allows_portfolio_at_minimum_diversification():
    picks = [
        {"score": 2.0, "inn": "7701"},
        {"score": 1.0, "inn": "7702"},
    ]

    result = bond_finder.allocate(picks, _allocation_cfg(min_issuers=2))

    assert result["issuers"] == 2
    assert result["constructible"] is True
    assert result["note"] is None
    assert abs(sum(result["weights"]) - 1.0) < 1e-3
