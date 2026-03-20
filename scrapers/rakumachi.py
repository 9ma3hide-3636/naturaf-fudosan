"""
楽待（rakumachi.jp）スクレイパー
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

BASE_URL = "https://www.rakumachi.jp"

# 対象都道府県コード（東京=13, 埼玉=11）
PREF_CODES = ["13", "11"]

# 一棟アパート・一棟マンション の物件種別コード
# 楽待: 1=一棟アパート, 2=一棟マンション
BUILDING_TYPES = [
    ("1", "一棟アパート"),
    ("2", "一棟マンション"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.rakumachi.jp/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get_html(url: str, params: Optional[Dict] = None) -> Optional[BeautifulSoup]:
    """GETリクエストしてBeautifulSoupを返す"""
    try:
        resp = SESSION.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.warning(f"[楽待] リクエスト失敗: {url} — {e}")
        return None


def _parse_man(text: str) -> Optional[float]:
    """'6,500万円' → 6500.0"""
    text = re.sub(r"[,，\s]", "", text)
    m = re.search(r"([\d.]+)万", text)
    if m:
        return float(m.group(1))
    m = re.search(r"([\d.]+)億", text)
    if m:
        return float(m.group(1)) * 10000
    return None


def _parse_yield(text: str) -> Optional[float]:
    """'7.23%' → 7.23"""
    m = re.search(r"([\d.]+)%", text)
    return float(m.group(1)) if m else None


def _parse_walk(text: str) -> Optional[int]:
    """'徒歩7分' → 7"""
    m = re.search(r"徒歩(\d+)分", text)
    return int(m.group(1)) if m else None


def _scrape_list_page(pref_code: str, building_type_code: str, page: int = 1) -> List[Dict]:
    """楽待の物件一覧ページをスクレイプ"""
    url = f"{BASE_URL}/syuueki/area/"
    params = {
        "pref_cd[]": pref_code,
        "property_type_cd[]": building_type_code,
        "price_min": 6000,
        "price_max": 8000,
        "walk": 10,
        "page": page,
        "sort": "new",
    }

    soup = _get_html(url, params)
    if not soup:
        return []

    properties = []

    # 物件カードのセレクタ（楽待の実際のHTML構造に基づく）
    cards = soup.select("li.property-list-item")
    if not cards:
        cards = soup.select("div.property-cassette")
    if not cards:
        cards = soup.select("article.property")

    logger.info(f"[楽待] pref={pref_code} type={building_type_code} page={page} → {len(cards)}件")

    for card in cards:
        prop = _parse_card(card, pref_code, building_type_code)
        if prop:
            properties.append(prop)

    return properties


def _parse_card(card, pref_code: str, building_type_code: str) -> Optional[Dict]:
    """個別物件カードをパース"""
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
            card.select_one(".cassette-title")
            or card.select_one(".property-name")
            or card.select_one("h2")
            or card.select_one("h3")
        )
        name = name_el.get_text(strip=True) if name_el else "（名称不明）"

        price_el = (
            card.select_one(".price")
            or card.select_one("[class*='price']")
        )
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_man(price_text)

        yield_el = (
            card.select_one(".yield")
            or card.select_one("[class*='yield']")
            or card.select_one("[class*='rimawari']")
        )
        yield_text = yield_el.get_text(strip=True) if yield_el else ""
        yield_pct = _parse_yield(yield_text)

        area_el = card.select_one(".area") or card.select_one("[class*='area']")
        area_text = area_el.get_text(strip=True) if area_el else ("東京都" if pref_code == "13" else "埼玉県")

        station_el = card.select_one(".station") or card.select_one("[class*='station']")
        station_text = station_el.get_text(strip=True) if station_el else ""

        walk_el = card.select_one(".walk") or card.select_one("[class*='walk']")
        walk_text = (walk_el.get_text(strip=True) if walk_el else "") or station_text
        walk_min = _parse_walk(walk_text)

        structure = _infer_structure(card, building_type_code)

        return {
            "site": "楽待",
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
        logger.debug(f"[楽待] カードパースエラー: {e}")
        return None


def _infer_structure(card, building_type_code: str) -> str:
    text = card.get_text()
    if "RC" in text or "鉄筋コンクリート" in text:
        return "RC一棟アパート"
    if "軽量鉄骨" in text:
        return "軽量鉄骨一棟アパート"
    if "鉄骨" in text or "S造" in text:
        return "鉄骨一棟アパート"
    if building_type_code == "2":
        return "一棟マンション"
    return "木造一棟アパート"


def _extract_station_name(text: str) -> str:
    m = re.search(r"(.+?駅)", text)
    return m.group(1) if m else text.split("徒歩")[0].strip()


def fetch_properties() -> Iterator[Dict]:
    """楽待から物件情報を全て取得してイテレートする"""
    for pref_code in PREF_CODES:
        for type_code, _ in BUILDING_TYPES:
            page = 1
            while True:
                props = _scrape_list_page(pref_code, type_code, page)
                yield from props

                if not props or page >= 5:
                    break
                if len(props) < 20:
                    break

                page += 1
                time.sleep(random.uniform(2.0, 4.0))

            time.sleep(random.uniform(1.5, 3.0))
