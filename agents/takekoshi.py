"""
竹越さん（ハンター）エージェント
元ソフトバンクエンジニア・不動産資産20億超。
データと論理で物件を分析し割安物件を瞬時に見抜く。
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from scrapers import rakumachi, kenbiya

logger = logging.getLogger(__name__)

PROPERTIES_DIR = Path(__file__).parent.parent / "properties"

# ────────────────────────────────────────────
# 監視条件
# ────────────────────────────────────────────
PRICE_MIN = 4000   # 万円
PRICE_MAX = 10000
WALK_MAX  = 10     # 徒歩分

YIELD_THRESHOLD = {
    "木造一棟アパート":     6.5,
    "軽量鉄骨一棟アパート": 6.2,
    "RC一棟アパート":       5.8,
    "一棟マンション":       5.8,
    "default":              6.5,
}

VALID_PREFS = ("東京都", "埼玉県")


# ────────────────────────────────────────────
# 相場分析
# ────────────────────────────────────────────

def _estimate_market(prop: Dict) -> Tuple[int, float]:
    """
    相場価格と割安率を推計する。
    同じ収益を「基準利回り」で割り戻した価格を相場とみなす。
    例: 実際7.5%・木造基準6.5% → 相場は価格×7.5/6.5
    返り値: (market_avg_price_man, discount_rate_pct)
    """
    structure = prop.get("structure", "default")
    yield_pct = prop.get("yield_pct") or 0.0
    price_man = prop.get("price") or 0.0

    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    if threshold <= 0 or yield_pct <= 0:
        return int(price_man), 0.0

    market_man = price_man * yield_pct / threshold
    discount = (market_man - price_man) / market_man * 100
    return int(market_man), round(discount, 1)


# ────────────────────────────────────────────
# 割安スコア計算
# ────────────────────────────────────────────

def _calc_score(prop: Dict) -> int:
    score = 50

    structure = prop.get("structure", "default")
    yield_pct = prop.get("yield_pct") or 0.0
    walk_min  = prop.get("walk_min") or 10
    price     = prop.get("price") or 0.0

    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    yield_premium = yield_pct - threshold
    score += min(40, int(yield_premium * 20))

    if price > 0:
        price_ratio = (PRICE_MAX - price) / (PRICE_MAX - PRICE_MIN)
        score += int(max(0.0, price_ratio) * 10)

    if walk_min <= 3:   score += 10
    elif walk_min <= 5: score += 7
    elif walk_min <= 7: score += 4
    elif walk_min <= 10: score += 1

    if "RC" in structure or "マンション" in structure:
        score += 5

    return max(0, min(100, score))


def _gen_comment(prop: Dict, score: int, discount_rate: float) -> str:
    structure = prop.get("structure", "")
    yield_pct = prop.get("yield_pct") or 0.0
    walk_min  = prop.get("walk_min") or 10
    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    premium   = yield_pct - threshold

    parts = []

    if discount_rate >= 10:
        parts.append(f"相場より{discount_rate:.0f}%安い。明確な割安物件。")
    elif discount_rate >= 5:
        parts.append(f"相場より{discount_rate:.0f}%程度の割安感。")
    else:
        parts.append("相場との乖離は小さい。利回り基準での選別が重要。")

    if premium >= 1.0:
        parts.append(f"基準利回りを{premium:.1f}pt超過。数字が明確に優位。")
    elif premium >= 0.5:
        parts.append(f"基準利回り+{premium:.1f}ptの余裕あり。")
    else:
        parts.append(f"基準利回りをギリギリクリア（+{premium:.1f}pt）。")

    if walk_min <= 5:
        parts.append(f"駅{walk_min}分は立地として申し分ない。")
    elif walk_min <= 7:
        parts.append(f"駅{walk_min}分は許容範囲内。")
    else:
        parts.append(f"駅{walk_min}分はやや遠い。出口戦略に影響する可能性あり。")

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
    price     = prop.get("price")
    yield_pct = prop.get("yield_pct")
    walk_min  = prop.get("walk_min")
    structure = prop.get("structure", "default")
    area      = prop.get("area", "")

    if price is None:     return False, "価格データなし"
    if yield_pct is None: return False, "利回りデータなし"
    if walk_min is None:  return False, "徒歩分数データなし"

    if not (PRICE_MIN <= price <= PRICE_MAX):
        return False, f"価格範囲外: {price:.0f}万円"
    if walk_min > WALK_MAX:
        return False, f"徒歩{walk_min}分（上限{WALK_MAX}分）"
    if not any(p in area for p in VALID_PREFS):
        return False, f"対象エリア外: {area}"

    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    if yield_pct < threshold:
        return False, f"利回り不足: {yield_pct:.1f}% < {threshold}%（{structure}）"

    return True, "OK"


# ────────────────────────────────────────────
# 物件JSON保存
# ────────────────────────────────────────────

def save_property(prop: Dict, prop_id: str) -> Path:
    """properties/{id}/property.json に保存してパスを返す"""
    prop_dir = PROPERTIES_DIR / prop_id
    prop_dir.mkdir(parents=True, exist_ok=True)

    market_man, discount_rate = _estimate_market(prop)

    data = {
        "id":                prop_id,
        "title":             prop.get("name", "（名称不明）"),
        "price":             int((prop.get("price") or 0) * 10000),   # 万円→円
        "yield_pct":         prop.get("yield_pct"),
        "walk_min":          prop.get("walk_min"),
        "structure":         prop.get("structure", ""),
        "rooms":             prop.get("rooms"),
        "age_years":         prop.get("age_years"),
        "area":              prop.get("area", ""),
        "population":        prop.get("population"),
        "url":               prop.get("url", ""),
        "takekoshi_score":   prop.get("score"),
        "takekoshi_comment": prop.get("comment", ""),
        "market_avg_price":  market_man * 10000,                       # 万円→円
        "discount_rate":     discount_rate,
        "found_at":          datetime.now().isoformat(),
        "site":              prop.get("site", ""),
        "station":           prop.get("station", ""),
    }

    out = prop_dir / "property.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"[竹越] 物件保存: {out}")
    return out


# ────────────────────────────────────────────
# 公開インターフェース
# ────────────────────────────────────────────

def hunt() -> Iterator[Dict]:
    """楽待・健美家から物件を取得し条件通過物件をイテレートする"""
    sources = [
        ("楽待", rakumachi.fetch_properties()),
        ("健美家", kenbiya.fetch_properties()),
    ]

    for site_name, generator in sources:
        logger.info(f"[竹越] {site_name} スキャン開始")
        count_total = 0
        count_pass  = 0

        for prop in generator:
            count_total += 1
            ok, reason = _passes_filter(prop)
            if not ok:
                logger.debug(f"[竹越] スキップ ({reason}): {prop.get('url', '')}")
                continue

            count_pass += 1
            market_man, discount_rate = _estimate_market(prop)
            score   = _calc_score(prop)
            comment = _gen_comment(prop, score, discount_rate)

            prop["score"]         = score
            prop["comment"]       = comment
            prop["market_man"]    = market_man
            prop["discount_rate"] = discount_rate
            yield prop

        logger.info(
            f"[竹越] {site_name} スキャン完了: "
            f"{count_total}件中 {count_pass}件が条件通過"
        )
