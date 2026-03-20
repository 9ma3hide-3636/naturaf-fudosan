"""
健美家（kenbiya.com）スクレイパー
一棟アパート・一棟マンション（埼玉・東京）を取得
"""
import time
import random
import logging
import re
from typing import Dict, Iterator, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kenbiya.com"

# 健美家の都道府県キー
PREF_CODES = {
    "13": "tokyo",
    "11": "saitama",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.kenbiya.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get_html(url: str, params: Optional[Dict] = None) -> Optional[BeautifulSoup]:
    try:
        resp = SESSION.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.warning(f"[健美家] リクエスト失敗: {url} — {e}")
        return None


def _parse_man(text: str) -> Optional[float]:
    text = re.sub(r"[,，\s]", "", text)
    m = re.search(r"([\d.]+)万", text)
    if m:
        return float(m.group(1))
    m = re.search(r"([\d.]+)億", text)
    if m:
        return float(m.group(1)) * 10000
    return None


def _parse_yield(text: str) -> Optional[float]:
    m = re.search(r"([\d.]+)%", text)
    return float(m.group(1)) if m else None


def _parse_walk(text: str) -> Optional[int]:
    m = re.search(r"徒歩(\d+)分", text)
    return int(m.group(1)) if m else None


def _scrape_list_page(pref_key: str, page: int = 1) -> List[Dict]:
    """健美家の物件一覧ページをスクレイプ"""
    url = f"{BASE_URL}/ar/ls/{pref_key}-apartment/"
    params = {
        "sell_price_min": 6000,
        "sell_price_max": 8000,
        "walk_min": 10,
        "pg": page,
        "sort": "new",
    }

    soup = _get_html(url, params)
    if not soup:
        return []

    properties = []

    cards = soup.select("div.property-item")
    if not cards:
        cards = soup.select("li.cassette")
    if not cards:
        cards = soup.select("div.cassette-body")
    if not cards:
        cards = soup.select("tr.property-row")

    logger.info(f"[健美家] pref={pref_key} page={page} → {len(cards)}件")

    for card in cards:
        prop = _parse_card(card, pref_key)
        if prop:
            properties.append(prop)

    return properties


def _parse_card(card, pref_key: str) -> Optional[Dict]:
    try:
        a_tag = card.select_one("a[href]")
        if not a_tag:
            return None
        href = a_tag.get("href", "")
        if href.startswith("/"):
            href = BASE_URL + href
        if not href:
            return None

        name_el = (
            card.select_one(".property-name")
            or card.select_one(".cassette-name")
            or card.select_one("h2")
            or card.select_one("h3")
            or card.select_one(".item-name")
        )
        name = name_el.get_text(strip=True) if name_el else "（名称不明）"

        price_el = (
            card.select_one(".price")
            or card.select_one("[class*='price']")
            or card.select_one(".sell-price")
        )
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_man(price_text)

        yield_el = (
            card.select_one(".yield")
            or card.select_one("[class*='yield']")
            or card.select_one("[class*='rimawari']")
            or card.select_one(".gross-yield")
        )
        yield_text = yield_el.get_text(strip=True) if yield_el else ""
        yield_pct = _parse_yield(yield_text)

        area_el = card.select_one(".address") or card.select_one("[class*='address']")
        area_text = area_el.get_text(strip=True) if area_el else (
            "東京都" if pref_key == "tokyo" else "埼玉県"
        )

        station_el = (
            card.select_one(".station")
            or card.select_one("[class*='station']")
            or card.select_one(".access")
        )
        station_text = station_el.get_text(strip=True) if station_el else ""
        walk_min = _parse_walk(station_text)

        structure = _infer_structure(card)

        return {
            "site": "健美家",
            "name": name,
            "structure": structure,
            "price": price,
            "yield_pct": yield_pct,
            "area": area_text,
            "station": _extract_station_name(station_text),
            "walk_min": walk_min,
            "url": href,
        }

    except Exception as e:
        logger.debug(f"[健美家] カードパースエラー: {e}")
        return None


def _infer_structure(card) -> str:
    text = card.get_text()
    if "RC" in text or "鉄筋コンクリート" in text:
        return "RC一棟アパート"
    if "軽量鉄骨" in text:
        return "軽量鉄骨一棟アパート"
    if "鉄骨" in text or "S造" in text:
        return "鉄骨一棟アパート"
    if "マンション" in text:
        return "一棟マンション"
    return "木造一棟アパート"


def _extract_station_name(text: str) -> str:
    m = re.search(r"(.+?駅)", text)
    return m.group(1) if m else text.split("徒歩")[0].strip()


def fetch_properties() -> Iterator[Dict]:
    """健美家から物件情報を全て取得してイテレートする"""
    for pref_key in PREF_CODES.values():
        page = 1
        while True:
            props = _scrape_list_page(pref_key, page)
            yield from props

            if not props or page >= 5:
                break
            if len(props) < 20:
                break

            page += 1
            time.sleep(random.uniform(2.0, 4.0))

        time.sleep(random.uniform(1.5, 3.0))
