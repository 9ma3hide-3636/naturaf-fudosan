"""
青柳さん（出口戦略）エージェント
元銀行員32歳・不動産資産20億。
売却シミュレーション・税金計算・最適タイミングを弾く。
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

# 譲渡所得税率（短期：5年以内39.63%、長期：5年超20.315%）
TAX_RATE_SHORT = 0.3963
TAX_RATE_LONG  = 0.20315

# 構造別年間価格下落率推計
PRICE_DECLINE_RATE = {
    "木造一棟アパート":     0.020,
    "軽量鉄骨一棟アパート": 0.018,
    "RC一棟アパート":       0.010,
    "一棟マンション":       0.008,
    "default":              0.020,
}


def _simulate_exit(
    prop: Dict,
    watanabe: Dict,
    hold_years: int,
) -> Dict:
    """hold_years年後の売却シミュレーション"""
    price_yen   = prop["price"]
    yield_pct   = prop["yield_pct"]
    structure   = prop.get("structure", "default")
    age_years   = prop.get("age_years") or 15

    # 年間CF（満室シナリオの年間CF）
    annual_cf = watanabe["cf_scenarios"]["満室"]["annual_cf"]

    # 売却価格推計（収益還元法：残存耐用年数に応じて利回りが悪化→価格下落）
    decline_rate = PRICE_DECLINE_RATE.get(structure, PRICE_DECLINE_RATE["default"])
    sale_price   = price_yen * (1 - decline_rate) ** hold_years

    # 取得費（簡易：物件価格の95%を取得費として計上）
    acquisition_cost = price_yen * 0.95

    # 譲渡益
    capital_gain = sale_price - acquisition_cost

    # 税率（売却時の所有期間で判定：5年超なら長期税率）
    tax_rate = TAX_RATE_LONG if hold_years > 5 else TAX_RATE_SHORT
    capital_gain_tax = max(0.0, capital_gain * tax_rate)

    # インカムゲイン合計
    total_income = annual_cf * hold_years

    # トータルリターン
    total_return = total_income + (sale_price - price_yen) - capital_gain_tax

    # 年率リターン（XIRR簡易版：総リターン/取得コスト/年数）
    annualized = total_return / price_yen / hold_years if price_yen > 0 else 0.0

    return {
        "hold_years":         hold_years,
        "estimated_sale_price": round(sale_price),
        "price_decline_total_pct": round(decline_rate * hold_years * 100, 1),
        "capital_gain":       round(capital_gain),
        "tax_rate":           tax_rate,
        "capital_gain_tax":   round(capital_gain_tax),
        "total_income_cf":    round(total_income),
        "total_return":       round(total_return),
        "annualized_return_pct": round(annualized * 100, 2),
        "note": "5年以内は短期譲渡税率39.63%、5年超は長期20.315%",
    }


def _best_timing(simulations: Dict) -> str:
    """最適売却タイミングを推奨"""
    best_year  = max(simulations, key=lambda y: simulations[y]["total_return"])
    best_return = simulations[best_year]["total_return"]
    if best_return < 0:
        return f"{best_year}年後が相対的に最良だが、トータルはマイナス。購入価格・利回りの再検討を推奨。"
    return f"{best_year}年後売却が最大リターン（{int(best_return/10000):,}万円）。5年超で長期税率が適用されるタイミングを狙う。"


def analyze(prop_path: Path) -> Optional[Dict]:
    watanabe_path = prop_path.parent / "watanabe-report.json"
    if not watanabe_path.exists():
        logger.warning(f"[青柳] watanabe-report.json 未存在: {prop_path.parent}")
        return None

    with open(prop_path, encoding="utf-8") as f:
        prop = json.load(f)
    with open(watanabe_path, encoding="utf-8") as f:
        watanabe = json.load(f)

    prop_id = prop["id"]

    simulations = {}
    for years in [5, 7, 10]:
        simulations[str(years)] = _simulate_exit(prop, watanabe, years)

    best = _best_timing(simulations)

    # ─── 青柳コメント ───
    c = []
    yr5 = simulations["5"]
    yr10 = simulations["10"]

    if yr5["capital_gain"] < 0:
        c.append("5年以内売却では譲渡損。短期保有は避けること。")
    else:
        c.append(f"5年売却で短期課税{int(TAX_RATE_SHORT*100)}%でも譲渡益{int(yr5['capital_gain']/10000):,}万円。")

    if yr10["annualized_return_pct"] >= 5.0:
        c.append(f"10年保有の年率リターン{yr10['annualized_return_pct']:.1f}%は優秀。長期保有戦略も有効。")
    elif yr10["annualized_return_pct"] >= 2.0:
        c.append(f"10年保有の年率{yr10['annualized_return_pct']:.1f}%。インカム重視の安定運用。")
    else:
        c.append(f"10年保有でも年率{yr10['annualized_return_pct']:.1f}%。出口価格の低下が響く。早期売却検討。")

    c.append(best)

    report = {
        "property_id":        prop_id,
        "analyzed_at":        datetime.now().isoformat(),
        "exit_simulations":   simulations,
        "optimal_timing":     best,
        "comment":            "".join(c),
    }

    out_path = prop_path.parent / "aoyagi-report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[青柳] レポート保存: {out_path}")

    _notify(prop, report)
    return report


def _notify(prop: Dict, report: Dict):
    from notifier.gmail import send_email
    price_man = int(prop["price"] / 10000)
    sims      = report["exit_simulations"]

    subject = (
        f"【青柳さん 出口戦略】{prop.get('title', prop['id'])}｜"
        f"10年リターン{sims['10']['annualized_return_pct']:.1f}%/年"
    )
    lines = [
        "【青柳さんより 出口戦略・売却シミュレーション】",
        "",
        f"物件：{prop.get('title', prop['id'])}",
        f"価格：{price_man:,}万円｜利回り：{prop['yield_pct']}%",
        "",
        "── 売却シミュレーション ──",
    ]
    for y, s in sims.items():
        tax_label = "短期課税" if s["hold_years"] <= 5 else "長期課税"
        lines += [
            f"  【{y}年後】",
            f"    売却価格：{int(s['estimated_sale_price']/10000):,}万円（価格下落{s['price_decline_total_pct']:.1f}%）",
            f"    譲渡益：{int(s['capital_gain']/10000):,}万円｜税率：{int(s['tax_rate']*100):.1f}%（{tax_label}）",
            f"    CF累計：{int(s['total_income_cf']/10000):,}万円",
            f"    トータルリターン：{int(s['total_return']/10000):,}万円（年率{s['annualized_return_pct']:.1f}%）",
        ]
    lines += [
        "",
        f"最適タイミング：{report['optimal_timing']}",
        "",
        f"青柳コメント：{report['comment']}",
        "",
        "─" * 40,
        "NATURAF不動産支部 AIシステム",
    ]
    send_email(subject, "\n".join(lines))


def run_all():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not PROPERTIES_DIR.exists():
        logger.info("[青柳] properties/ ディレクトリなし — スキップ")
        return
    count = 0
    for prop_file in sorted(PROPERTIES_DIR.glob("*/property.json")):
        report_file = prop_file.parent / "aoyagi-report.json"
        if report_file.exists():
            continue
        if not (prop_file.parent / "watanabe-report.json").exists():
            logger.debug(f"[青柳] watanabe未完了スキップ: {prop_file.parent.name}")
            continue
        try:
            analyze(prop_file)
            count += 1
        except Exception as e:
            logger.error(f"[青柳] 分析エラー {prop_file}: {e}")
    logger.info(f"[青柳] 完了 — {count}件分析")


if __name__ == "__main__":
    run_all()
