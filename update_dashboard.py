"""
司令室ダッシュボード自動更新スクリプト
properties/*/のレポートを読み込み、index.htmlの物件カードセクションを更新する
"""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT           = Path(__file__).parent
PROPERTIES_DIR = ROOT / "properties"
INDEX_HTML     = ROOT / "index.html"
MARKER_START   = "<!-- PROPERTY_CARDS_START -->"
MARKER_END     = "<!-- PROPERTY_CARDS_END -->"


# ────────────────────────────────────────────
# レポート読み込み
# ────────────────────────────────────────────

def load_reports(prop_dir: Path) -> Dict:
    reports = {}
    for key, fname in [
        ("property",   "property.json"),
        ("watanabe",   "watanabe-report.json"),
        ("kaneko",     "kaneko-report.json"),
        ("aoyagi",     "aoyagi-report.json"),
        ("takahashi",  "takahashi-report.json"),
    ]:
        path = prop_dir / fname
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    reports[key] = json.load(f)
            except Exception as e:
                logger.warning(f"読み込みエラー {path}: {e}")
    return reports


def get_judgment_style(judgment: Optional[str]):
    if judgment == "銀行へGO！":
        return ("go",    "🟢", "#4adb9a", "#0d3020", "#1a5030")
    elif judgment == "要検討":
        return ("review","🟡", "#f0c040", "#2a2000", "#4a3a00")
    else:
        return ("pass",  "🔴", "#f05050", "#2a0d0d", "#4a1a1a")


def progress_bar(score: int, color: str) -> str:
    pct = max(0, min(100, score))
    return f"""
<div style="background:#0a1220;border-radius:6px;height:10px;margin:6px 0 2px;">
  <div style="width:{pct}%;height:10px;background:{color};border-radius:6px;transition:width 0.6s;"></div>
</div>"""


def cf_row(name: str, monthly_cf: float) -> str:
    man   = monthly_cf / 10000
    color = "#4adb9a" if monthly_cf >= 0 else "#f05050"
    mark  = "✓" if monthly_cf >= 0 else "✗"
    return (
        f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
        f'border-bottom:1px solid #1a2a3a;font-size:12px;">'
        f'<span style="color:#c0d0e0;">{mark} {name}</span>'
        f'<span style="color:{color};font-weight:bold;">{man:+.1f}万円/月</span></div>'
    )


def agent_badge(label: str, emoji: str, available: bool) -> str:
    color = "#4a9eff" if available else "#2a3a4a"
    bg    = "#0d1a2e" if available else "#0a1220"
    return (
        f'<span style="background:{bg};color:{color};border:1px solid {color};'
        f'border-radius:12px;padding:3px 10px;font-size:11px;margin:2px;">'
        f'{emoji} {label}</span>'
    )


def render_agent_detail(emoji: str, name: str, comment: str, analyzed_at: str) -> str:
    ts = analyzed_at[:16].replace("T", " ") if analyzed_at else ""
    return f"""
<details style="margin:4px 0;">
  <summary style="cursor:pointer;color:#4a7fa5;font-size:12px;padding:6px 0;
    list-style:none;display:flex;align-items:center;gap:6px;">
    <span style="color:#4a9eff;">▶</span> {emoji} {name}
    <span style="color:#3a5a7a;font-size:10px;margin-left:auto;">{ts}</span>
  </summary>
  <div style="background:#060d18;border-radius:6px;padding:10px;margin-top:4px;
    font-size:12px;color:#c0d0e0;line-height:1.7;border-left:2px solid #1e3550;">
    {comment}
  </div>
</details>"""


def render_card(prop_id: str, reports: Dict) -> str:
    prop      = reports.get("property", {})
    watanabe  = reports.get("watanabe", {})
    kaneko    = reports.get("kaneko", {})
    aoyagi    = reports.get("aoyagi", {})
    takahashi = reports.get("takahashi", {})

    title     = prop.get("title", prop_id)
    price_man = int((prop.get("price") or 0) / 10000)
    yield_pct = prop.get("yield_pct") or 0.0
    discount  = prop.get("discount_rate") or 0.0
    area      = prop.get("area", "")
    station   = prop.get("station", "")
    walk_min  = prop.get("walk_min", "?")
    structure = prop.get("structure", "")
    url       = prop.get("url", "#")
    found_at  = (prop.get("found_at") or "")[:16].replace("T", " ")

    # 判断ステータス
    judgment = takahashi.get("judgment") if takahashi else None
    _, emoji, accent, bg_color, border_color = get_judgment_style(judgment)

    status_label = judgment if judgment else ("分析中..." if reports else "取得中")

    # スコア
    final_score = takahashi.get("scoring", {}).get("final_score", 0) if takahashi else 0

    # CF ストレステスト
    cf_html = ""
    if watanabe and "cf_scenarios" in watanabe:
        sc = watanabe["cf_scenarios"]
        cf_html = "".join([
            cf_row("満室",       sc.get("満室",       {}).get("monthly_cf", 0)),
            cf_row("稼働70%",    sc.get("稼働70%",    {}).get("monthly_cf", 0)),
            cf_row("金利+2%",    sc.get("金利2%増",   {}).get("monthly_cf", 0)),
            cf_row("最悪ケース", sc.get("最悪ケース", {}).get("monthly_cf", 0)),
            cf_row("修繕後",     sc.get("修繕後",     {}).get("monthly_cf", 0)),
        ])

    # エージェントバッジ
    badges = "".join([
        agent_badge("竹越",    "🔍", "property"   in reports),
        agent_badge("渡邉",    "💰", "watanabe"   in reports),
        agent_badge("金子",    "📋", "kaneko"     in reports),
        agent_badge("青柳",    "🔭", "aoyagi"     in reports),
        agent_badge("高橋代表","👑", "takahashi"  in reports),
    ])

    # 相談ログ（各エージェントのコメント）
    log_html = ""
    if "property" in reports and prop.get("takekoshi_comment"):
        log_html += render_agent_detail(
            "🔍", "竹越さん（ハンター）",
            prop.get("takekoshi_comment", ""),
            prop.get("found_at", ""),
        )
    if watanabe:
        log_html += render_agent_detail(
            "💰", "渡邉さん（融資・財務）",
            watanabe.get("comment", ""),
            watanabe.get("analyzed_at", ""),
        )
    if kaneko:
        log_html += render_agent_detail(
            "📋", "金子コーチ（資料作成）",
            kaneko.get("comment", ""),
            kaneko.get("analyzed_at", ""),
        )
    if aoyagi:
        log_html += render_agent_detail(
            "🔭", "青柳さん（出口戦略）",
            aoyagi.get("comment", ""),
            aoyagi.get("analyzed_at", ""),
        )
    if takahashi:
        log_html += render_agent_detail(
            "👑", "高橋代表（最終判断）",
            takahashi.get("comment", ""),
            takahashi.get("analyzed_at", ""),
        )

    # 出口サマリ（aoyagi）
    exit_html = ""
    if aoyagi and "exit_simulations" in aoyagi:
        sims = aoyagi["exit_simulations"]
        rows = []
        for y in ["5", "7", "10"]:
            s   = sims.get(y, {})
            ret = s.get("total_return", 0)
            ann = s.get("annualized_return_pct", 0)
            color = "#4adb9a" if ret >= 0 else "#f05050"
            rows.append(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:4px 0;border-bottom:1px solid #1a2a3a;font-size:12px;">'
                f'<span style="color:#c0d0e0;">{y}年後売却</span>'
                f'<span style="color:{color};">{int(ret/10000):,}万円（年率{ann:.1f}%）</span></div>'
            )
        exit_html = f"""
<div style="margin-top:12px;">
  <div style="font-size:12px;color:#4a7fa5;margin-bottom:6px;">🔭 出口シミュレーション</div>
  {"".join(rows)}
</div>"""

    # スコアセクション
    score_section = ""
    if takahashi:
        sc_detail = takahashi.get("scoring", {})
        bar       = progress_bar(final_score, accent)
        score_section = f"""
<div style="margin:14px 0;padding:12px;background:#0a1220;border-radius:8px;border:1px solid #1a3050;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
    <span style="font-size:12px;color:#4a7fa5;">👑 高橋代表スコア</span>
    <span style="font-size:22px;font-weight:bold;color:{accent};">{final_score}<span style="font-size:12px;color:#5b8fc9;">/100</span></span>
  </div>
  {bar}
  <div style="font-size:10px;color:#3a5a7a;margin-top:4px;">
    利回:{sc_detail.get('yield',{}).get('score','?')} 立地:{sc_detail.get('location',{}).get('score','?')} 融資積算:{sc_detail.get('loan_assessed',{}).get('score','?')} 空室:{sc_detail.get('vacancy_risk',{}).get('score','?')} 築年:{sc_detail.get('age_remaining',{}).get('score','?')} 出口:{sc_detail.get('exit_strategy',{}).get('score','?')}
    &nbsp;|&nbsp;減点:{sc_detail.get('deductions','0')}
  </div>
</div>"""

    return f"""
<div style="background:#0d1625;border:2px solid {border_color};border-radius:12px;
  padding:20px;margin-bottom:20px;position:relative;">

  <!-- ヘッダー -->
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
    <div>
      <div style="background:{bg_color};color:{accent};border:1px solid {border_color};
        display:inline-block;padding:3px 12px;border-radius:20px;font-size:12px;font-weight:bold;margin-bottom:8px;">
        {emoji} {status_label}
      </div>
      <div style="font-size:16px;font-weight:bold;color:#fff;">{title}</div>
      <div style="font-size:12px;color:#5b8fc9;margin-top:4px;">{area}｜{station} 徒歩{walk_min}分</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:20px;font-weight:bold;color:#4a9eff;">{price_man:,}<span style="font-size:12px;color:#5b8fc9;">万円</span></div>
      <div style="font-size:13px;color:#4adb9a;margin-top:2px;">利回り {yield_pct:.1f}%</div>
      <div style="font-size:11px;color:#f0a030;margin-top:2px;">相場比 -{discount:.0f}%割安</div>
    </div>
  </div>

  <div style="font-size:11px;color:#2a4a6a;margin-bottom:12px;">
    {structure}｜発見: {found_at}
    <a href="{url}" target="_blank" style="color:#2a5a9b;margin-left:8px;text-decoration:none;">🔗 物件詳細</a>
  </div>

  {score_section}

  <!-- CFストレステスト -->
  {"" if not cf_html else f'''<div style="margin:12px 0;"><div style="font-size:12px;color:#4a7fa5;margin-bottom:6px;">💰 CFストレステスト（月次）</div>{cf_html}</div>'''}

  {exit_html}

  <!-- エージェントバッジ -->
  <div style="margin:14px 0 10px;display:flex;flex-wrap:wrap;gap:4px;">
    <span style="font-size:11px;color:#4a7fa5;margin-right:4px;">分析状況：</span>
    {badges}
  </div>

  <!-- 相談ログ -->
  {"" if not log_html else f'''<div style="margin-top:12px;border-top:1px solid #1a2a3a;padding-top:12px;"><div style="font-size:12px;color:#4a7fa5;margin-bottom:6px;">💬 相談ログ</div>{log_html}</div>'''}

</div>"""


# ────────────────────────────────────────────
# HTML生成・注入
# ────────────────────────────────────────────

def generate_section(all_reports: list) -> str:
    if not all_reports:
        return f"""
{MARKER_START}
<div style="text-align:center;padding:40px 24px;color:#2a4a6a;font-size:14px;">
  物件スキャン待機中... 次回スキャンまでしばらくお待ちください。
</div>
{MARKER_END}"""

    cards = "".join(render_card(pid, rpts) for pid, rpts in all_reports)
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""{MARKER_START}
<style>
  .prop-section-header{{padding:16px 24px 0;}}
  .prop-section-header h2{{font-size:16px;color:#c0d0e0;display:flex;align-items:center;gap:8px;}}
  details>summary::-webkit-details-marker{{display:none;}}
  details[open]>summary span:first-child{{transform:rotate(90deg);display:inline-block;}}
</style>
<div class="prop-section-header">
  <h2>🏠 物件分析レポート
    <span style="background:#1a2744;color:#4a9eff;padding:3px 10px;border-radius:12px;font-size:11px;border:1px solid #2a4a8a;">
      {len(all_reports)}件
    </span>
    <span style="font-size:11px;color:#3a5a7a;margin-left:auto;">最終更新: {now}</span>
  </h2>
</div>
<div style="padding:16px 24px;">
{cards}
</div>
{MARKER_END}"""


def update_html(section_html: str):
    content = INDEX_HTML.read_text(encoding="utf-8")
    start   = content.find(MARKER_START)
    end     = content.find(MARKER_END)

    if start == -1 or end == -1:
        logger.error("index.html にマーカーが見つかりません")
        return False

    new_content = content[:start] + section_html + content[end + len(MARKER_END):]
    INDEX_HTML.write_text(new_content, encoding="utf-8")
    logger.info("index.html 更新完了")
    return True


def git_push():
    if not os.getenv("GITHUB_ACTIONS"):
        logger.info("ローカル実行 — git push スキップ")
        return
    try:
        subprocess.run(["git", "config", "user.email", "bot@naturaf.jp"], check=True)
        subprocess.run(["git", "config", "user.name",  "NATURAF Bot"],    check=True)
        subprocess.run(["git", "add", "index.html"],                       check=True)
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", "[ダッシュボード] 物件カード更新"], check=True)
            subprocess.run(["git", "push"], check=True)
            logger.info("git push 完了")
    except subprocess.CalledProcessError as e:
        logger.error(f"git push 失敗: {e}")


# ────────────────────────────────────────────
# エントリーポイント
# ────────────────────────────────────────────

def main():
    if not PROPERTIES_DIR.exists():
        logger.info("properties/ なし — 空セクションで更新")
        update_html(generate_section([]))
        return

    all_reports = []
    for prop_dir in sorted(PROPERTIES_DIR.iterdir(), reverse=True):
        if not prop_dir.is_dir() or prop_dir.name.startswith("."):
            continue
        reports = load_reports(prop_dir)
        if not reports:
            continue
        all_reports.append((prop_dir.name, reports))

    logger.info(f"物件数: {len(all_reports)}件")
    section = generate_section(all_reports)
    update_html(section)
    git_push()


if __name__ == "__main__":
    main()
