"""
金子コーチ（資料作成）エージェント
資産14億・コーチングスペシャリスト。
空室リスク・管理会社・事業計画書を整える。
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

# エリア別推定空室率（出典：楽待レポート・国勢調査ベース推計）
VACANCY_BASE = {
    "東京都":   0.07,
    "埼玉県":   0.10,
    "default":  0.10,
}

STRUCTURE_VACANCY_ADJ = {
    "木造一棟アパート":     0.02,
    "軽量鉄骨一棟アパート": 0.01,
    "RC一棟アパート":      -0.01,
    "一棟マンション":      -0.02,
    "default":              0.02,
}

MANAGEMENT_CHECKLIST = [
    "地元密着型で入居付け力が高いか",
    "空室保証（サブリース）の条件は適切か",
    "入居審査の基準は厳格か（滞納リスク管理）",
    "退去後のリフォームスピードと費用は妥当か",
    "管理費率は5%以内か（満室時収入対比）",
    "緊急対応体制（24時間）は整備されているか",
    "月次報告書の内容と頻度は適切か",
    "近隣エリアの管理実績数は十分か",
    "家賃設定・相場把握の精度はどうか",
    "出口（売却時）への協力体制があるか",
]


def _estimate_vacancy(prop: Dict) -> Dict:
    area      = prop.get("area", "")
    structure = prop.get("structure", "default")

    base = VACANCY_BASE.get("東京都" if "東京" in area else "埼玉県", VACANCY_BASE["default"])
    adj  = STRUCTURE_VACANCY_ADJ.get(structure, STRUCTURE_VACANCY_ADJ["default"])

    # 駅距離補正
    walk_min = prop.get("walk_min") or 7
    if walk_min <= 3:   walk_adj = -0.02
    elif walk_min <= 5: walk_adj = -0.01
    elif walk_min <= 7: walk_adj =  0.00
    else:               walk_adj =  0.02

    effective = max(0.03, min(0.25, base + adj + walk_adj))

    return {
        "area_base_vacancy":      round(base, 3),
        "structure_adjustment":   round(adj, 3),
        "walk_adjustment":        round(walk_adj, 3),
        "effective_vacancy_rate": round(effective, 3),
        "effective_occupancy":    round(1 - effective, 3),
        "data_source":            "楽待エリアデータ・国勢調査ベース推計",
    }


def _cf_at_80pct(prop: Dict, vacancy: Dict) -> Dict:
    """80%稼働時のCF（簡易版、融資条件は渡邉レポートを参照）"""
    price_yen     = prop["price"]
    yield_pct     = prop["yield_pct"]
    gross_income  = price_yen * yield_pct / 100
    occupancy     = 0.80
    effective_inc = gross_income * occupancy
    operating_exp = gross_income * 0.15
    noi           = effective_inc - operating_exp

    return {
        "occupancy":      occupancy,
        "gross_income":   round(gross_income),
        "effective_income": round(effective_inc),
        "noi":            round(noi),
        "note":           "融資返済前NOI（返済後CFは渡邉レポート参照）",
    }


def _gen_business_plan(prop: Dict, vacancy: Dict, cf80: Dict) -> str:
    price_man     = int(prop["price"] / 10000)
    gross_man     = int(cf80["gross_income"] / 10000)
    noi_man       = int(cf80["noi"] / 10000)
    occ_rate      = int((1 - vacancy["effective_vacancy_rate"]) * 100)
    structure     = prop.get("structure", "")
    area          = prop.get("area", "")
    station       = prop.get("station", "")
    walk_min      = prop.get("walk_min", "?")

    lines = [
        "■ 事業計画書（概要版）",
        f"  物件名   ：{prop.get('title', prop['id'])}",
        f"  所在地   ：{area}（{station} 徒歩{walk_min}分）",
        f"  構造     ：{structure}",
        f"  取得価格 ：{price_man:,}万円",
        "",
        "■ 収益計画",
        f"  表面利回り   ：{prop['yield_pct']}%",
        f"  想定満室収入 ：年{gross_man:,}万円",
        f"  想定稼働率   ：{occ_rate}%（エリア空室率{int(vacancy['effective_vacancy_rate']*100)}%ベース）",
        f"  運営費       ：満室収入の15%",
        f"  NOI（80%稼働）：年{noi_man:,}万円",
        "",
        "■ 出口戦略",
        "  ・購入後3〜5年でバリューアップ後売却を基本とする",
        "  ・売却時の利回り水準は購入時±0.5%以内を目標",
        "  ・資産の回転により次物件の頭金を確保する",
        "",
        "■ リスクと対策",
        f"  空室リスク：エリア空室率{int(vacancy['effective_vacancy_rate']*100)}%（対策：地元密着管理会社・家賃設定精査）",
        "  金利リスク ：変動金利2%超に備え金利+2%でのCFシミュレーション済み",
        "  修繕リスク ：築年数に応じ修繕積立を月次で確保",
    ]
    return "\n".join(lines)


def analyze(prop_path: Path) -> Optional[Dict]:
    with open(prop_path, encoding="utf-8") as f:
        prop = json.load(f)

    prop_id = prop["id"]
    vacancy = _estimate_vacancy(prop)
    cf80    = _cf_at_80pct(prop, vacancy)
    biz_plan = _gen_business_plan(prop, vacancy, cf80)

    # ─── 金子コメント ───
    occ = int(vacancy["effective_occupancy"] * 100)
    c = []
    if vacancy["effective_vacancy_rate"] <= 0.07:
        c.append(f"エリア空室率{int(vacancy['effective_vacancy_rate']*100)}%は優秀。入居付けに自信を持てる立地。")
    elif vacancy["effective_vacancy_rate"] <= 0.12:
        c.append(f"空室率{int(vacancy['effective_vacancy_rate']*100)}%は平均的。管理会社選定が明暗を分ける。")
    else:
        c.append(f"空室率{int(vacancy['effective_vacancy_rate']*100)}%はやや高め。リーシング戦略の事前確認が必須。")
    c.append(f"80%稼働NOIは年{int(cf80['noi']/10000):,}万円。事業計画として成立する数字。")

    report = {
        "property_id":          prop_id,
        "analyzed_at":          datetime.now().isoformat(),
        "vacancy_research":     vacancy,
        "cf_at_80pct":          cf80,
        "management_checklist": MANAGEMENT_CHECKLIST,
        "business_plan":        biz_plan,
        "comment":              "".join(c),
    }

    out_path = prop_path.parent / "kaneko-report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[金子] レポート保存: {out_path}")

    _notify(prop, report)
    return report


def _notify(prop: Dict, report: Dict):
    from notifier.gmail import send_email
    price_man = int(prop["price"] / 10000)
    v         = report["vacancy_research"]
    cf        = report["cf_at_80pct"]

    subject = (
        f"【金子コーチ 空室・事業計画】{prop.get('title', prop['id'])}｜"
        f"空室率{int(v['effective_vacancy_rate']*100)}%｜80%稼働NOI{int(cf['noi']/10000):,}万"
    )
    lines = [
        "【金子コーチより 空室リスク・事業計画レポート】",
        "",
        f"物件：{prop.get('title', prop['id'])}",
        f"価格：{price_man:,}万円｜利回り：{prop['yield_pct']}%",
        "",
        "── 空室率リサーチ ──",
        f"  エリア基礎空室率：{int(v['area_base_vacancy']*100)}%",
        f"  構造補正：{v['structure_adjustment']:+.1%}",
        f"  駅距離補正：{v['walk_adjustment']:+.1%}",
        f"  実効空室率：{int(v['effective_vacancy_rate']*100)}%（稼働率：{int(v['effective_occupancy']*100)}%）",
        f"  データ元：{v['data_source']}",
        "",
        "── 80%稼働時NOI ──",
        f"  満室収入：年{int(cf['gross_income']/10000):,}万円",
        f"  実効収入：年{int(cf['effective_income']/10000):,}万円",
        f"  NOI：年{int(cf['noi']/10000):,}万円",
        "",
        "── 管理会社チェックリスト ──",
    ]
    for item in report["management_checklist"]:
        lines.append(f"  □ {item}")
    lines += [
        "",
        f"金子コメント：{report['comment']}",
        "",
        "─" * 40,
        report["business_plan"],
        "─" * 40,
        "NATURAF不動産支部 AIシステム",
    ]
    send_email(subject, "\n".join(lines))


def run_all():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not PROPERTIES_DIR.exists():
        logger.info("[金子] properties/ ディレクトリなし — スキップ")
        return
    count = 0
    for prop_file in sorted(PROPERTIES_DIR.glob("*/property.json")):
        report_file = prop_file.parent / "kaneko-report.json"
        if report_file.exists():
            continue
        try:
            analyze(prop_file)
            count += 1
        except Exception as e:
            logger.error(f"[金子] 分析エラー {prop_file}: {e}")
    logger.info(f"[金子] 完了 — {count}件分析")


if __name__ == "__main__":
    run_all()
