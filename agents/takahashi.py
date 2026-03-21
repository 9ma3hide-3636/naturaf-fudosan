"""
高橋代表（総司令塔）エージェント
不動産資産100億超。
全エージェントのレポートを統合し最終判断を下す。
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PROPERTIES_DIR = ROOT / "properties"

YIELD_THRESHOLD = {
    "木造一棟アパート":     6.5,
    "軽量鉄骨一棟アパート": 6.2,
    "RC一棟アパート":       5.8,
    "一棟マンション":       5.8,
    "default":              6.5,
}

LEGAL_LIFE = {
    "木造一棟アパート":     22,
    "軽量鉄骨一棟アパート": 27,
    "RC一棟アパート":       47,
    "一棟マンション":       47,
    "default":              22,
}


def _score_yield(prop: Dict) -> Tuple[int, str]:
    """利回りスコア（20点満点）"""
    yield_pct = prop.get("yield_pct") or 0.0
    structure = prop.get("structure", "default")
    threshold = YIELD_THRESHOLD.get(structure, YIELD_THRESHOLD["default"])
    premium   = yield_pct - threshold

    if premium >= 2.0:   s, note = 20, f"基準+{premium:.1f}pt（優秀）"
    elif premium >= 1.0: s, note = 16, f"基準+{premium:.1f}pt（良好）"
    elif premium >= 0.5: s, note = 12, f"基準+{premium:.1f}pt（可）"
    elif premium >= 0.0: s, note =  8, f"基準+{premium:.1f}pt（ギリギリ）"
    else:                s, note =  0, f"基準未達（{premium:.1f}pt）"
    return s, note


def _score_location(prop: Dict) -> Tuple[int, str]:
    """立地・人口スコア（20点満点）"""
    population = prop.get("population") or 0
    walk_min   = prop.get("walk_min") or 10
    area       = prop.get("area", "")

    pop_score  = 10 if population >= 500_000 \
                 else 8 if population >= 200_000 \
                 else 6 if population >= 100_000 else 3
    walk_score = 10 if walk_min <= 3 \
                 else 8 if walk_min <= 5 \
                 else 6 if walk_min <= 7 else 3

    # 東京ボーナス
    bonus = 2 if "東京" in area else 0
    s     = min(20, pop_score + walk_score + bonus - 10)   # 2軸10点満点ずつ→合計20
    # 再計算: pop(0-10) + walk(0-10) + tokyo(0-2) をスケール
    s = min(20, int((pop_score + walk_score) / 20 * 18) + bonus)
    note = f"人口{int(population/10000) if population else '?'}万・駅{walk_min}分"
    return s, note


def _score_loan_assessed(watanabe: Dict) -> Tuple[int, str]:
    """融資・積算スコア（20点満点）"""
    ratio = watanabe["assessed_value"]["vs_price_ratio"]
    eq_ok = watanabe["equity_check"]["is_ok"]

    if ratio >= 1.0:   r_score = 12
    elif ratio >= 0.8: r_score = 10
    elif ratio >= 0.6: r_score =  7
    else:              r_score =  4

    e_score = 8 if eq_ok else 3
    s = min(20, r_score + e_score)
    note = f"積算比{int(ratio*100)}%・自己資金{'OK' if eq_ok else 'NG'}"
    return s, note


def _score_vacancy(kaneko: Dict) -> Tuple[int, str]:
    """空室リスクスコア（15点満点）"""
    vac_rate = kaneko["vacancy_research"]["effective_vacancy_rate"]

    if vac_rate <= 0.05:   s = 15
    elif vac_rate <= 0.08: s = 12
    elif vac_rate <= 0.12: s = 8
    elif vac_rate <= 0.15: s = 5
    else:                  s = 2
    note = f"推定空室率{int(vac_rate*100)}%"
    return s, note


def _score_age_remaining(prop: Dict, watanabe: Dict) -> Tuple[int, str]:
    """築年数・残耐用年数スコア（15点満点）"""
    remaining  = watanabe["remaining_life"]["remaining"]
    loan_term  = watanabe["remaining_life"]["max_loan_term"]

    if remaining >= 30:   s = 15
    elif remaining >= 20: s = 12
    elif remaining >= 15: s = 9
    elif remaining >= 10: s = 6
    elif remaining >= 5:  s = 3
    else:                 s = 1
    note = f"残耐用{remaining}年・最大融資{loan_term}年"
    return s, note


def _score_exit(aoyagi: Dict) -> Tuple[int, str]:
    """出口戦略スコア（10点満点）"""
    yr10 = aoyagi["exit_simulations"]["10"]
    ret  = yr10["annualized_return_pct"]

    if ret >= 8.0:   s = 10
    elif ret >= 5.0: s = 8
    elif ret >= 3.0: s = 6
    elif ret >= 1.0: s = 4
    else:            s = 1
    note = f"10年年率{ret:.1f}%"
    return s, note


def _stress_deductions(watanabe: Dict) -> Tuple[int, str]:
    """ストレステストによる減点"""
    stress  = watanabe["stress_flags"]
    deduct  = 0
    reasons = []

    # 稼働70%で▲5万超 → 即見送り扱い（-99点）
    if stress["occupancy70_neg_5man"]:
        return -99, "⚠ 稼働70%でCF▲5万超（即見送り基準）"

    if stress["rate_plus2_negative"]:
        deduct -= 10
        reasons.append("金利+2%マイナス(-10)")
    if stress["worst_negative"]:
        deduct -= 5
        reasons.append("最悪ケースマイナス(-5)")
    if stress["after_repair_negative"]:
        deduct -= 5
        reasons.append("修繕後マイナス(-5)")

    return deduct, "・".join(reasons) if reasons else "ストレス全クリア"


def analyze(prop_path: Path) -> Optional[Dict]:
    # 全レポート必須
    watanabe_path = prop_path.parent / "watanabe-report.json"
    kaneko_path   = prop_path.parent / "kaneko-report.json"
    aoyagi_path   = prop_path.parent / "aoyagi-report.json"

    missing = [p.name for p in [watanabe_path, kaneko_path, aoyagi_path] if not p.exists()]
    if missing:
        logger.warning(f"[高橋] 未完了レポートあり: {missing} — {prop_path.parent.name}")
        return None

    with open(prop_path,      encoding="utf-8") as f: prop     = json.load(f)
    with open(watanabe_path,  encoding="utf-8") as f: watanabe = json.load(f)
    with open(kaneko_path,    encoding="utf-8") as f: kaneko   = json.load(f)
    with open(aoyagi_path,    encoding="utf-8") as f: aoyagi   = json.load(f)

    prop_id = prop["id"]

    # ─── スコアリング ───
    y_score,  y_note  = _score_yield(prop)
    l_score,  l_note  = _score_location(prop)
    la_score, la_note = _score_loan_assessed(watanabe)
    v_score,  v_note  = _score_vacancy(kaneko)
    a_score,  a_note  = _score_age_remaining(prop, watanabe)
    e_score,  e_note  = _score_exit(aoyagi)

    base_score = y_score + l_score + la_score + v_score + a_score + e_score

    deduct, stress_note = _stress_deductions(watanabe)
    immediate_reject    = deduct == -99

    final_score = max(0, base_score + deduct) if not immediate_reject else 0

    # ─── 最終判断 ───
    if immediate_reject:
        judgment = "見送り"
        judgment_en = "PASS"
    elif final_score >= 80:
        judgment = "銀行へGO！"
        judgment_en = "GO"
    elif final_score >= 60:
        judgment = "要検討"
        judgment_en = "REVIEW"
    else:
        judgment = "見送り"
        judgment_en = "PASS"

    # ─── 高橋コメント ───
    price_man = int(prop["price"] / 10000)
    c = []
    c.append(f"総合スコア{final_score}点。")
    if immediate_reject:
        c.append("稼働70%でCFが大幅マイナス。英之さんの今の純資産では融資後の持ち堪えが厳しい。数字が全てを語っている。")
    elif final_score >= 80:
        c.append(f"利回り・立地・融資・出口の全軸で合格水準。埼玉縣信用金庫へ持ち込む価値がある物件だ。{price_man:,}万円で動ける資産背景と合っている。")
    elif final_score >= 70:
        c.append("概ね良い数字だが一点突破の強みに欠ける。あと一歩詰める交渉（値引き or 利回り改善）ができれば Go に変わる。")
    elif final_score >= 60:
        c.append("弱点が複数ある。今の英之さんのフェーズでは、もう少し数字の良い物件に絞るべきだ。")
    else:
        c.append("複数の基準を下回っている。10年で20億の目標に貢献できる物件ではない。次を探せ。")

    report = {
        "property_id": prop_id,
        "analyzed_at": datetime.now().isoformat(),
        "scoring": {
            "yield":        {"score": y_score,  "max": 20, "note": y_note},
            "location":     {"score": l_score,  "max": 20, "note": l_note},
            "loan_assessed":{"score": la_score, "max": 20, "note": la_note},
            "vacancy_risk": {"score": v_score,  "max": 15, "note": v_note},
            "age_remaining":{"score": a_score,  "max": 15, "note": a_note},
            "exit_strategy":{"score": e_score,  "max": 10, "note": e_note},
            "base_total":   base_score,
            "deductions":   deduct,
            "stress_note":  stress_note,
            "final_score":  final_score,
        },
        "immediate_reject": immediate_reject,
        "judgment":         judgment,
        "judgment_en":      judgment_en,
        "comment":          "".join(c),
    }

    out_path = prop_path.parent / "takahashi-report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[高橋] レポート保存: {out_path}")

    _notify_final(prop, watanabe, kaneko, aoyagi, report)
    return report


def _notify_final(prop, watanabe, kaneko, aoyagi, report):
    from notifier.gmail import send_email
    price_man   = int(prop["price"] / 10000)
    sc          = report["scoring"]
    judgment    = report["judgment"]
    final_score = sc["final_score"]

    if judgment == "銀行へGO！":
        emoji = "🟢"
    elif judgment == "要検討":
        emoji = "🟡"
    else:
        emoji = "🔴"

    subject = (
        f"{emoji}【高橋代表 最終判断】{judgment}｜"
        f"{prop.get('title', prop['id'])}｜{final_score}点"
    )

    sims = aoyagi["exit_simulations"]
    lines = [
        f"{'='*50}",
        f"  高橋代表 最終判断：{judgment}（{final_score}点）",
        f"{'='*50}",
        "",
        f"物件：{prop.get('title', prop['id'])}",
        f"価格：{price_man:,}万円｜利回り：{prop['yield_pct']}%｜{prop.get('structure','')}",
        f"エリア：{prop.get('area','')}｜{prop.get('station','')} 徒歩{prop.get('walk_min','?')}分",
        "",
        "── スコアリング（100点満点） ──",
        f"  利回り       ：{sc['yield']['score']:>2}/{sc['yield']['max']}点  {sc['yield']['note']}",
        f"  立地・人口   ：{sc['location']['score']:>2}/{sc['location']['max']}点  {sc['location']['note']}",
        f"  融資・積算   ：{sc['loan_assessed']['score']:>2}/{sc['loan_assessed']['max']}点  {sc['loan_assessed']['note']}",
        f"  空室リスク   ：{sc['vacancy_risk']['score']:>2}/{sc['vacancy_risk']['max']}点  {sc['vacancy_risk']['note']}",
        f"  築年数・残耐 ：{sc['age_remaining']['score']:>2}/{sc['age_remaining']['max']}点  {sc['age_remaining']['note']}",
        f"  出口戦略     ：{sc['exit_strategy']['score']:>2}/{sc['exit_strategy']['max']}点  {sc['exit_strategy']['note']}",
        f"  ベース合計   ：{sc['base_total']}点",
        f"  ストレス減点 ：{sc['deductions']}点（{sc['stress_note']}）",
        f"  ────────────────",
        f"  最終スコア   ：{final_score}点",
        "",
        "── CF（渡邉さん）──",
    ]
    for name, s in watanabe["cf_scenarios"].items():
        mark = "✓" if s["monthly_cf"] >= 0 else "✗"
        lines.append(f"  {mark} {name}：{s['monthly_cf']/10000:+.1f}万円/月")
    lines += [
        "",
        "── 出口戦略（青柳さん）──",
    ]
    for y, s in sims.items():
        lines.append(
            f"  {y}年後売却：トータル{int(s['total_return']/10000):,}万円（年率{s['annualized_return_pct']:.1f}%）"
        )
    lines += [
        "",
        f"高橋コメント：{report['comment']}",
        "",
        "─" * 50,
        "NATURAF不動産支部 AIシステム Phase1",
        "次のアクション：",
    ]
    if judgment == "銀行へGO！":
        lines += [
            "  1. 埼玉縣信用金庫へ物件概要書を提出",
            "  2. 現地視察のスケジュール調整",
            "  3. 渡邉さんと融資戦略の最終確認",
        ]
    elif judgment == "要検討":
        lines += [
            "  1. 値引き交渉（目標：5%以上）の余地を確認",
            "  2. 管理会社の空室率保証条件を確認",
            "  3. 再分析後に判断",
        ]
    else:
        lines.append("  次の物件を待つ。")

    send_email(subject, "\n".join(lines))


def run_all():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not PROPERTIES_DIR.exists():
        logger.info("[高橋] properties/ ディレクトリなし — スキップ")
        return
    count = 0
    for prop_file in sorted(PROPERTIES_DIR.glob("*/property.json")):
        report_file = prop_file.parent / "takahashi-report.json"
        if report_file.exists():
            continue
        required = ["watanabe-report.json", "kaneko-report.json", "aoyagi-report.json"]
        if not all((prop_file.parent / r).exists() for r in required):
            logger.debug(f"[高橋] 前段レポート未完了スキップ: {prop_file.parent.name}")
            continue
        try:
            analyze(prop_file)
            count += 1
        except Exception as e:
            logger.error(f"[高橋] 分析エラー {prop_file}: {e}")
    logger.info(f"[高橋] 完了 — {count}件分析")


if __name__ == "__main__":
    run_all()
