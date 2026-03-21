"""
竹越さん（ハンター）エージェント
元ソフトバンクエンジニア・不動産資産20億超。
データと論理で物件を分析し割安物件を瞬時に見抜く。
"""
import logging
from typing import Dict, Iterator, List, Optional, Tuple

from scrapers import rakumachi, kenbiya

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 監視条件
# ────────────────────────────────────────────
PRICE_MIN = 4000  # 万円
PRICE_MAX = 10000
WALK_MAX = 10     # 徒歩分

# 構造別最低利回り
YIELD_THRESHOLD = {
    "木造一棟アパート":   6.5,
    "軽量鉄骨一棟アパート": 6.2,
    "RC一棟アパート":    5.8,
    "一棟マンション":    5.8,
    # 未分類のフォールバック
    "default":          6.5,
}

# エリア条件：対象都道府県（スクレイパー側でフィルタ済みだが念のため）
VALID_PREFS = ("東京都", "埼玉県")


# ────────────────────────────────────────────
# 割安スコア計算（竹越ロジック）
# ────────────────────────────────────────────

def _calc_score(prop: dict) -> int:
    """
    割安スコアを 0〜100 で算出。
    基準利回りを超えた余剰分・立地・徒歩距離を総合評価。
    """
    score = 50  # ベーススコア

    structure = prop.get("structure", "default")
    yield_pct = prop.get("yield_pct") or 0.0
    walk_min = prop.get("walk_min") or 10
    price = prop.get("price") or 0.0

    # ① 利回りプレミアム（最重要：40点満点）
    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    yield_premium = yield_pct - threshold
    yield_score = min(40, int(yield_premium * 20))  # 0.1%超過ごとに2点、上限40
    score += yield_score

    # ② 価格帯スコア（安いほど有利：10点満点）
    # 6000万に近いほど高得点
    if price > 0:
        price_ratio = (PRICE_MAX - price) / (PRICE_MAX - PRICE_MIN)
        score += int(price_ratio * 10)

    # ③ 徒歩距離スコア（近いほど有利：0〜10点）
    if walk_min <= 3:
        score += 10
    elif walk_min <= 5:
        score += 7
    elif walk_min <= 7:
        score += 4
    elif walk_min <= 10:
        score += 1

    # ④ 構造ボーナス（RC・マンションは資産性が高い）
    if "RC" in structure or "マンション" in structure:
        score += 5

    return max(0, min(100, score))


def _gen_comment(prop: dict, score: int) -> str:
    """竹越さんらしいコメントを生成"""
    structure = prop.get("structure", "")
    yield_pct = prop.get("yield_pct") or 0.0
    walk_min = prop.get("walk_min") or 10
    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    premium = yield_pct - threshold

    parts = []

    # 利回り評価
    if premium >= 1.0:
        parts.append(f"基準利回りを{premium:.1f}pt超過。数字が明確に優位。")
    elif premium >= 0.5:
        parts.append(f"基準利回り+{premium:.1f}ptの余裕あり。")
    else:
        parts.append(f"基準利回りをギリギリクリア（+{premium:.1f}pt）。")

    # 徒歩
    if walk_min <= 5:
        parts.append(f"駅{walk_min}分は立地として申し分ない。")
    elif walk_min <= 7:
        parts.append(f"駅{walk_min}分は許容範囲内。")
    else:
        parts.append(f"駅{walk_min}分はやや遠い。出口戦略に影響する可能性あり。")

    # 総合判断
    if score >= 80:
        parts.append("→ 即精査推奨。英之さんのポートフォリオに合致する可能性が高い。")
    elif score >= 65:
        parts.append("→ 要精査。数字は悪くないが詳細確認が必要。")
    else:
        parts.append("→ 参考情報として共有。慎重な検討を推奨。")

    return "".join(parts)


# ────────────────────────────────────────────
# フィルタリング
# ────────────────────────────────────────────

def _passes_filter(prop: Dict) -> Tuple[bool, str]:
    """
    監視条件を満たすか検証。
    (True/False, 理由文字列) を返す。
    """
    price = prop.get("price")
    yield_pct = prop.get("yield_pct")
    walk_min = prop.get("walk_min")
    structure = prop.get("structure", "default")
    area = prop.get("area", "")

    # 必須データ欠損チェック
    if price is None:
        return False, "価格データなし"
    if yield_pct is None:
        return False, "利回りデータなし"
    if walk_min is None:
        return False, "徒歩分数データなし"

    # 価格範囲
    if not (PRICE_MIN <= price <= PRICE_MAX):
        return False, f"価格範囲外: {price:.0f}万円"

    # 徒歩
    if walk_min > WALK_MAX:
        return False, f"徒歩{walk_min}分（上限{WALK_MAX}分）"

    # エリア（東京都・埼玉県）
    if not any(p in area for p in VALID_PREFS):
        return False, f"対象エリア外: {area}"

    # 利回り
    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    if yield_pct < threshold:
        return False, f"利回り不足: {yield_pct:.1f}% < {threshold}%（{structure}）"

    return True, "OK"


# ────────────────────────────────────────────
# 公開インターフェース
# ────────────────────────────────────────────

def hunt() -> Iterator[Dict]:
    """
    楽待・健美家から物件を取得し、
    条件を満たした物件にスコア・コメントを付与してイテレートする。
    """
    sources = [
        ("楽待", rakumachi.fetch_properties()),
        ("健美家", kenbiya.fetch_properties()),
    ]

    for site_name, generator in sources:
        logger.info(f"[竹越] {site_name} スキャン開始")
        count_total = 0
        count_pass = 0

        for prop in generator:
            count_total += 1
            ok, reason = _passes_filter(prop)

            if not ok:
                logger.debug(f"[竹越] スキップ ({reason}): {prop.get('url', '')}")
                continue

            count_pass += 1
            score = _calc_score(prop)
            comment = _gen_comment(prop, score)

            prop["score"] = score
            prop["comment"] = comment
            yield prop

        logger.info(
            f"[竹越] {site_name} スキャン完了: "
            f"{count_total}件中 {count_pass}件が条件通過"
        )
