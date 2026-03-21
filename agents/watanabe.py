"""
渡邉さん（融資・財務）エージェント
現役銀行員・5棟保有。
CF5パターン・積算価格・融資期間を数字で叩く。
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PROPERTIES_DIR = ROOT / "properties"

NET_WORTH_YEN = 1300 * 10000   # 英之さんの純資産（円）
BASE_RATE     = 0.02625        # 埼玉縣信用金庫
LTV           = 0.90           # 借入比率

LEGAL_LIFE = {
    "木造一棟アパート":     22,
    "軽量鉄骨一棟アパート": 27,
    "RC一棟アパート":       47,
    "一棟マンション":       47,
    "default":              22,
}

REPLACEMENT_COST_PER_SQM = {   # 円/㎡
    "木造一棟アパート":     150_000,
    "軽量鉄骨一棟アパート": 180_000,
    "RC一棟アパート":       250_000,
    "一棟マンション":       280_000,
    "default":              150_000,
}


# ────────────────────────────────────────────
# 財務計算ユーティリティ
# ────────────────────────────────────────────

def _monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    if years <= 0 or principal <= 0:
        return 0.0
    r = annual_rate / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def _cf_scenario(
    price_yen: float,
    yield_pct: float,
    occupancy: float,
    loan_amount: float,
    rate: float,
    loan_years: int,
    extra_annual: float = 0.0,
) -> Dict:
    gross_income      = price_yen * yield_pct / 100
    effective_income  = gross_income * occupancy
    # 運営費（管理5%・固都税5%・保険2%・修繕3% = 15%）
    operating_exp     = gross_income * 0.15
    noi               = effective_income - operating_exp
    monthly_pay       = _monthly_payment(loan_amount, rate, loan_years)
    annual_loan       = monthly_pay * 12
    annual_cf         = noi - annual_loan - extra_annual
    dscr              = noi / annual_loan if annual_loan > 0 else 999.0

    return {
        "occupancy":        occupancy,
        "rate":             rate,
        "gross_income":     round(gross_income),
        "effective_income": round(effective_income),
        "operating_exp":    round(operating_exp),
        "noi":              round(noi),
        "annual_loan":      round(annual_loan),
        "extra_cost":       round(extra_annual),
        "annual_cf":        round(annual_cf),
        "monthly_cf":       round(annual_cf / 12),
        "dscr":             round(dscr, 2),
    }


def _assessed_value(prop: Dict) -> Dict:
    structure  = prop.get("structure", "default")
    age_years  = prop.get("age_years") or 15
    rooms      = prop.get("rooms") or 8
    walk_min   = prop.get("walk_min") or 7
    area       = prop.get("area", "")

    building_area = rooms * 20 * 1.2   # 1室20㎡ + 共用20%
    land_area     = building_area * 1.5

    is_tokyo  = "東京" in area
    road_price = (500_000 if walk_min <= 5 else 350_000) if is_tokyo \
               else (250_000 if walk_min <= 5 else 180_000)

    legal_life    = LEGAL_LIFE.get(structure, LEGAL_LIFE["default"])
    remaining     = max(0, legal_life - age_years)
    replace_cost  = REPLACEMENT_COST_PER_SQM.get(structure, 150_000)

    land_val     = road_price * land_area
    building_val = replace_cost * building_area * (remaining / legal_life) if legal_life > 0 else 0
    total        = land_val + building_val

    return {
        "building_area_sqm":      round(building_area, 1),
        "land_area_sqm":          round(land_area, 1),
        "road_price_per_sqm":     road_price,
        "replace_cost_per_sqm":   replace_cost,
        "land_value":             round(land_val),
        "building_value":         round(building_val),
        "total_assessed":         round(total),
        "note":                   "推計値（路線価・面積とも概算）",
    }


# ────────────────────────────────────────────
# メイン分析
# ────────────────────────────────────────────

def analyze(prop_path: Path) -> Optional[Dict]:
    with open(prop_path, encoding="utf-8") as f:
        prop = json.load(f)

    prop_id   = prop["id"]
    price_yen = prop["price"]
    yield_pct = prop["yield_pct"]
    structure = prop.get("structure", "default")
    age_years = prop.get("age_years") or 15

    legal_life    = LEGAL_LIFE.get(structure, LEGAL_LIFE["default"])
    remaining     = max(1, legal_life - age_years)
    loan_term     = min(remaining, 30)
    loan_amount   = price_yen * LTV
    equity_req    = price_yen - loan_amount
    buffer        = NET_WORTH_YEN - equity_req

    repair_annual = price_yen * 0.03 / 5   # 物件価格3%を5年均等

    scenarios = {
        "満室":       _cf_scenario(price_yen, yield_pct, 1.00, loan_amount, BASE_RATE,        loan_term),
        "稼働70%":    _cf_scenario(price_yen, yield_pct, 0.70, loan_amount, BASE_RATE,        loan_term),
        "金利2%増":   _cf_scenario(price_yen, yield_pct, 1.00, loan_amount, BASE_RATE + 0.02, loan_term),
        "最悪ケース": _cf_scenario(price_yen, yield_pct, 0.50, loan_amount, BASE_RATE + 0.02, loan_term),
        "修繕後":     _cf_scenario(price_yen, yield_pct, 0.90, loan_amount, BASE_RATE,        loan_term, repair_annual),
    }

    stress = {
        "occupancy70_monthly_cf":  scenarios["稼働70%"]["monthly_cf"],
        "occupancy70_neg_5man":    scenarios["稼働70%"]["monthly_cf"] < -50_000,
        "rate_plus2_negative":     scenarios["金利2%増"]["monthly_cf"] < 0,
        "worst_negative":          scenarios["最悪ケース"]["monthly_cf"] < 0,
        "after_repair_negative":   scenarios["修繕後"]["monthly_cf"] < 0,
    }

    assessed      = _assessed_value(prop)
    assessed_ratio = assessed["total_assessed"] / price_yen if price_yen > 0 else 0
    assessed["vs_price_ratio"] = round(assessed_ratio, 2)

    # ─── 渡邉コメント ───
    c = []
    if buffer >= 0:
        c.append(f"自己資金{int(equity_req/10000):,}万に対し{int(NET_WORTH_YEN/10000):,}万確保。余裕{int(buffer/10000):,}万。")
    else:
        c.append(f"自己資金{int(equity_req/10000):,}万必要だが純資産{int(NET_WORTH_YEN/10000):,}万で不足{int(-buffer/10000):,}万。")
    if stress["occupancy70_neg_5man"]:
        c.append("稼働70%でCF▲5万超。銀行審査で厳しく見られる水準。")
    elif scenarios["稼働70%"]["monthly_cf"] >= 0:
        c.append("稼働70%でもCFプラス。融資審査で有利。")
    c.append(f"積算は物件価格の{int(assessed_ratio*100)}%。")
    if assessed_ratio >= 0.8:
        c.append("担保価値良好。")
    elif assessed_ratio >= 0.6:
        c.append("積算はやや低め。収益評価行との交渉が鍵。")
    else:
        c.append("積算低い。収益評価行一択。")

    report = {
        "property_id":  prop_id,
        "analyzed_at":  datetime.now().isoformat(),
        "loan": {
            "amount":          round(loan_amount),
            "equity_required": round(equity_req),
            "rate":            BASE_RATE,
            "term_years":      loan_term,
            "monthly_payment": round(_monthly_payment(loan_amount, BASE_RATE, loan_term)),
            "annual_payment":  round(_monthly_payment(loan_amount, BASE_RATE, loan_term) * 12),
        },
        "remaining_life": {
            "legal_life":    legal_life,
            "age_years":     age_years,
            "remaining":     remaining,
            "max_loan_term": loan_term,
        },
        "equity_check": {
            "required":  round(equity_req),
            "available": NET_WORTH_YEN,
            "buffer":    round(buffer),
            "is_ok":     buffer >= 0,
        },
        "assessed_value":  assessed,
        "cf_scenarios":    scenarios,
        "stress_flags":    stress,
        "comment":         "".join(c),
    }

    out_path = prop_path.parent / "watanabe-report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[渡邉] レポート保存: {out_path}")

    _notify(prop, report)
    return report


def _notify(prop: Dict, report: Dict):
    from notifier.gmail import send_email
    price_man = int(prop["price"] / 10000)
    s         = report["cf_scenarios"]
    stress    = report["stress_flags"]

    subject = (
        f"【渡邉さん 融資分析】{prop.get('title', prop['id'])}｜"
        f"融資{report['loan']['term_years']}年｜"
        f"{'✓' if report['equity_check']['is_ok'] else '✗'}自己資金"
    )
    lines = [
        "【渡邉さんより 融資・CF分析レポート】",
        "",
        f"物件：{prop.get('title', prop['id'])}",
        f"価格：{price_man:,}万円｜利回り：{prop['yield_pct']}%",
        "",
        "── 融資条件 ──",
        f"借入：{int(report['loan']['amount']/10000):,}万円｜金利：{BASE_RATE*100:.3f}%｜期間：{report['loan']['term_years']}年",
        f"月次返済：{int(report['loan']['monthly_payment']/10000):,.1f}万円",
        "",
        "── CF 5パターン（月次） ──",
    ]
    for name, sc in s.items():
        mark = "✓" if sc["monthly_cf"] >= 0 else "✗"
        lines.append(f"  {mark} {name}：{sc['monthly_cf']/10000:+.1f}万円/月（DSCR {sc['dscr']:.2f}）")
    lines += [
        "",
        "── ストレス判定 ──",
        f"  稼働70%で▲5万超：{'⚠ YES' if stress['occupancy70_neg_5man'] else 'OK'}",
        f"  金利+2%マイナス：{'⚠ YES' if stress['rate_plus2_negative'] else 'OK'}",
        f"  最悪ケースマイナス：{'⚠ YES' if stress['worst_negative'] else 'OK'}",
        f"  修繕後マイナス：{'⚠ YES' if stress['after_repair_negative'] else 'OK'}",
        "",
        f"積算：{int(report['assessed_value']['total_assessed']/10000):,}万円（物件価格の{int(report['assessed_value']['vs_price_ratio']*100)}%）",
        "",
        f"渡邉コメント：{report['comment']}",
        "",
        "─" * 40,
        "NATURAF不動産支部 AIシステム",
    ]
    send_email(subject, "\n".join(lines))


# ────────────────────────────────────────────
# スタンドアロン実行（GitHub Actions から呼ばれる）
# ────────────────────────────────────────────

def run_all():
    """未分析の物件を全件処理する"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not PROPERTIES_DIR.exists():
        logger.info("[渡邉] properties/ ディレクトリなし — スキップ")
        return

    count = 0
    for prop_file in sorted(PROPERTIES_DIR.glob("*/property.json")):
        report_file = prop_file.parent / "watanabe-report.json"
        if report_file.exists():
            logger.debug(f"[渡邉] 分析済みスキップ: {prop_file.parent.name}")
            continue
        try:
            analyze(prop_file)
            count += 1
        except Exception as e:
            logger.error(f"[渡邉] 分析エラー {prop_file}: {e}")

    logger.info(f"[渡邉] 完了 — {count}件分析")


if __name__ == "__main__":
    run_all()
