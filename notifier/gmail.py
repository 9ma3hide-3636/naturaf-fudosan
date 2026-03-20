"""
Gmail SMTP 通知モジュール
Googleアカウントのアプリパスワードを使ってSMTP送信する
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GMAIL_ADDRESS  = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASSWORD", "")
NOTIFY_TO      = os.getenv("NOTIFY_TO", GMAIL_ADDRESS)  # 未設定なら送信元に届く

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(subject: str, body: str) -> bool:
    """GmailのSMTPでメールを送信。成功時True"""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASS:
        logger.warning("[Gmail] GMAIL_ADDRESS または GMAIL_APP_PASSWORD が未設定 — 通知をスキップします")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = NOTIFY_TO

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_ADDRESS, NOTIFY_TO, msg.as_string())
        logger.info(f"[Gmail] 送信完了 → {NOTIFY_TO}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("[Gmail] 認証エラー — アプリパスワードを確認してください")
        return False
    except Exception as e:
        logger.error(f"[Gmail] 送信エラー: {e}")
        return False


def format_property_message(prop: dict) -> tuple:
    """物件情報をメールの (件名, 本文) に整形"""
    site      = prop.get("site", "")
    name      = prop.get("name", "（名称不明）")
    structure = prop.get("structure", "")
    price_man = int(prop.get("price") or 0)
    yield_pct = prop.get("yield_pct") or 0.0
    area      = prop.get("area", "")
    station   = prop.get("station", "")
    walk      = prop.get("walk_min", "?")
    url       = prop.get("url", "")
    score     = prop.get("score", "?")
    comment   = prop.get("comment", "")

    subject = f"【竹越さん 新着物件アラート】{name}｜{yield_pct:.1f}%｜{price_man:,}万円"

    body = "\n".join([
        "【竹越さんより新着物件アラート🔍】",
        "",
        f"サイト    ：{site}",
        f"物件名    ：{name}",
        f"構造      ：{structure}",
        f"価格      ：{price_man:,}万円",
        f"利回り    ：{yield_pct:.1f}%",
        f"エリア    ：{area}",
        f"最寄駅    ：{station} 徒歩{walk}分",
        f"URL       ：{url}",
        f"割安スコア：{score}/100",
        f"竹越コメント：{comment}",
        "",
        "─" * 40,
        "NATURAF不動産支部 AIシステム Phase1",
        "担当：竹越さん（ハンター）",
    ])

    return subject, body
