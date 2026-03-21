"""
NATURAF不動産支部 AIシステム Phase1
エントリーポイント（竹越さん担当）

動作フロー:
  1. DB初期化
  2. 竹越さんが楽待・健美家を巡回
  3. 新着物件のみDBに登録 + properties/{id}/property.json に保存
  4. 初回実行は通知なし
  5. 2回目以降: Gmail通知 + GitHub Actions 上では git push
     → push が他エージェントのワークフローをトリガー
"""
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from database import db
from agents import takekoshi
from notifier.gmail import send_email, format_property_message


def make_prop_id(prop: dict) -> str:
    """物件IDを生成: 20260322-abc12345"""
    date_str = datetime.now().strftime("%Y%m%d")
    short_id = db.make_id(prop.get("url", ""))[:8]
    return f"{date_str}-{short_id}"


def is_first_run() -> bool:
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    return count == 0


def git_commit_push(prop_id: str):
    """GitHub Actions 上でのみ実行: property.json をコミット＆プッシュ"""
    if not os.getenv("GITHUB_ACTIONS"):
        return
    try:
        subprocess.run(["git", "config", "user.email", "bot@naturaf.jp"],   check=True)
        subprocess.run(["git", "config", "user.name",  "NATURAF Bot"],      check=True)
        subprocess.run(["git", "add", "properties/"],                        check=True)
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"[竹越] 新着物件 {prop_id}"],
                check=True,
            )
            subprocess.run(["git", "push"], check=True)
            logger.info(f"[main] git push 完了: {prop_id}")
    except subprocess.CalledProcessError as e:
        logger.error(f"[main] git push 失敗: {e}")


def main():
    logger.info("=" * 50)
    logger.info("NATURAF不動産支部 AIシステム Phase1 起動")
    logger.info("担当：竹越さん（ハンター）")
    logger.info("=" * 50)

    db.init_db()

    first_run = is_first_run()
    if first_run:
        logger.info("【初回実行】全物件をDBに登録のみ（通知なし）")

    # ─── スキャン ───
    new_props = []
    for prop in takekoshi.hunt():
        url = prop.get("url", "")
        if not url:
            continue

        if db.is_new(url):
            prop_id = make_prop_id(prop)
            db.insert_property(prop, notified=False)
            saved_path = takekoshi.save_property(prop, prop_id)
            new_props.append((prop_id, prop, saved_path))
            logger.info(
                f"[新着] {prop.get('site')} | {prop.get('name', '?')} | "
                f"{prop.get('yield_pct', '?')}% | スコア:{prop.get('score', '?')}"
            )

    logger.info(f"スキャン完了 — 新着物件: {len(new_props)}件")

    # ─── 初回実行 ───
    if first_run:
        for prop_row in db.get_unnotified():
            db.mark_notified(prop_row["url"])
        logger.info("初回実行完了。次回から新着物件を通知します。")
        return

    # ─── Gmail通知 + git push（GitHub Actions 上のみ）───
    for prop_id, prop, saved_path in new_props:
        subject, body = format_property_message(prop)
        if send_email(subject, body):
            db.mark_notified(prop.get("url", ""))
        git_commit_push(prop_id)

    if not new_props:
        logger.info("新着通知対象なし")


if __name__ == "__main__":
    main()
