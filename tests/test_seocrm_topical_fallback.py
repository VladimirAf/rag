"""Topical fallback для seocrm_article при malfunction_howto."""

import importlib.util
from pathlib import Path


def _load_topical_fallback():
    path = Path(__file__).resolve().parents[1] / "routes/files/topical_fallback.py"
    spec = importlib.util.spec_from_file_location("topical_fallback_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tf = _load_topical_fallback()

QUERY_5713 = (
    "У блендера после мытья стала подтекать крышка стакана. "
    "Что проверить и если нужны детали или аксессуары, укажи точные позиции"
)
TITLE_1240 = "Пластиковый стакан блендера: как пользоваться и ухаживать без лишних сложностей"
EXCERPT_1240 = (
    "в этих случаях разумно рассмотреть замену стакана и при необходимости "
    "обратиться в сервисный центр для подбора совместимой ёмкости"
)


def test_allows_only_malfunction_intent():
    assert tf.allows_seocrm_topical_fallback("malfunction_howto")
    assert not tf.allows_seocrm_topical_fallback("manual_usage")
    assert not tf.allows_seocrm_topical_fallback(None)


def test_topical_match_strength_article_1240():
    s = tf.topical_match_strength(
        QUERY_5713,
        "seocrm_article_1240",
        TITLE_1240,
        EXCERPT_1240,
    )
    assert s >= tf.TOPICAL_FALLBACK_MIN_STRENGTH


def test_topical_match_strength_irrelevant_low():
    s = tf.topical_match_strength(
        QUERY_5713,
        "seocrm_article_999",
        "Как выбрать кофемашину",
        "обзор рынка кофемашин для офиса",
    )
    assert s < tf.TOPICAL_FALLBACK_MIN_STRENGTH


def test_topical_select_picks_article_1240():
    candidates = [
        {
            "filename": "seocrm_article_999",
            "text_title": "Кофемашины",
            "excerpt": "выбор кофемашины для дома",
            "file_id": "a",
        },
        {
            "filename": "seocrm_article_1240",
            "text_title": TITLE_1240,
            "excerpt": EXCERPT_1240,
            "file_id": "b",
        },
    ]
    indexes, strength = tf.topical_select_candidate_indexes(candidates, QUERY_5713)
    assert indexes == [1]
    assert strength >= tf.TOPICAL_FALLBACK_MIN_STRENGTH
