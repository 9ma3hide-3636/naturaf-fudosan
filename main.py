"""
NATURAF不動産支部 AIシステム Phase1
エントリーポイント

動作フロー:
  1. DB初期化
  2. 竹越さん（ハンター）が楽待・健美家を巡回
  3. 新着物件のみDBに登録
  4. 初回実行（全件が新規）は通知しない
  5. 2回目以降: 未通知物件をGmailへ送信
"""
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# パス設定
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from database import db
from agents import takekoshi
from notifier.gmail import send_email, format_property_message


def is_first_run() -> bool:
    """DBが空（初回実行）かどうかを判定"""
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    return count == 0


def main():
    logger.info("=" * 50)
    logger.info("NATURAF不動産支部 AIシステム Phase1 起動")
    logger.info("担当：竹越さん（ハンター）")
    logger.info("=" * 50)

    # DB初期化
    db.init_db()

    first_run = is_first_run()
    if first_run:
        logger.info("【初回実行】全物件をDBに登録のみ（Gmail通知なし）")

    # ─────────────────────────────────────
    # 竹越さんによるスキャン＆フィルタリング
    # ─────────────────────────────────────
    new_count = 0
    for prop in takekoshi.hunt():
        url = prop.get("url", "")
        if not url:
            continue

        if db.is_new(url):
            db.insert_property(prop, notified=False)
            new_count += 1
            logger.info(
                f"[新着] {prop.get('site')} | {prop.get('name', '?')} | "
                f"{prop.get('yield_pct', '?')}% | スコア:{prop.get('score', '?')}"
            )
        else:
            logger.debug(f"[既存] スキップ: {url}")

    logger.info(f"スキャン完了 — 新着物件: {new_count}件")

    # ─────────────────────────────────────
    # Gmail通知（初回実行は送らない）
    # ─────────────────────────────────────
    if first_run:
        # 初回実行分を全て「通知済み」にマーク（次回から差分通知）
        for prop_row in db.get_unnotified():
            db.mark_notified(prop_row["url"])
        logger.info("初回実行完了。次回から新着物件をGmailへ通知します。")
        return

    unnotified = db.get_unnotified()
    if not unnotified:
        logger.info("新着通知対象なし")
        return

    logger.info(f"Gmail通知対象: {len(unnotified)}件")
    success_count = 0
    for prop_row in unnotified:
        subject, body = format_property_message(prop_row)
        if send_email(subject, body):
            db.mark_notified(prop_row["url"])
            success_count += 1
        else:
            logger.warning(f"通知失敗: {prop_row.get('url', '')}")

    logger.info(f"Gmail通知完了: {success_count}/{len(unnotified)}件")


if __name__ == "__main__":
    main()
