"""
楽待（rakumachi.jp）スクレイパー
RSS経由で新着取得 → 個別ページで詳細取得
"""
import time
import random
import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, Iterator, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rakumachi.jp"
PREF_CODES = ["13", "11"]
BUILDING_TYPES = [("1", "一棟アパート"), ("2", "一棟マンション")]

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


def _fetch_rss_urls(pref_code: str, type_code: str) -> List[str]:
    """RSSから物件URLリストを取得（403ブロックなし）"""
    rss_url = f"{BASE_URL}/syuueki/area/rss/"
    params = {
        "pref_cd[]": pref_code,
        "property_type_cd[]": type_code,
        "price_min": 4000,
        "price_max": 10000,
        "walk": 10,
        "sort": "new",
    }
    try:
        resp = SESSION.get(rss_url, params=params, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        urls = []
        for item in root.findall(".//item"):
            link = item.findtext("link")
            if link:
                urls.append(link.strip())
        logger.info(f"[楽待RSS] pref={pref_code} type={type_code} → {len(urls)}件のURL取得")
        return urls
    except Exception as e:
        logger.warning(f"[楽待RSS] 取得失敗: {e}")
        return []


def _fetch_detail(url: str, pref_code: str, type_code: str) -> Optional[Dict]:
    """個別物件ページから詳細情報を取得"""
    try:
        time.sleep(random.uniform(2.0, 4.0))  # 礼儀正しく待機
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 物件名
        name_el = soup.select_one("h1") or soup.select_one(".property-name")
        name = name_el.get_text(strip=True) if name_el else "（名称不明）"

        # 価格
        price_el = soup.select_one("[class*='price']")
        price = _parse_man(price_el.get_text(strip=True)) if price_el else None

        # 利回り
        yield_el = soup.select_one("[class*='yield']") or soup.select_one("[class*='rimawari']")
        yield_pct = _parse_yield(yield_el.get_text(strip=True)) if yield_el else None

        # 駅・徒歩
        full_text = soup.get_text()
        walk_min = _parse_walk(full_text)

        station_m = re.search(r"(.+?駅)", full_text)
        station = station_m.group(1) if station_m else ""

        # エリア
        area = "東京都" if pref_code == "13" else "埼玉県"

        # 構造
        structure = "木造一棟アパート"
        if "RC" in full_text or "鉄筋コンクリート" in full_text:
            structure = "RC一棟アパート"
        elif "軽量鉄骨" in full_text:
            structure = "軽量鉄骨一棟アパート"
        elif type_code == "2":
            structure = "一棟マンション"

        return {
            "site": "楽待",
            "name": name,
            "structure": structure,
            "price": price,
            "yield_pct": yield_pct,
            "area": area,
            "station": station,
            "walk_min": walk_min,
            "url": url,
        }

    except Exception as e:
        logger.debug(f"[楽待] 詳細取得エラー {url}: {e}")
        return None


def fetch_properties() -> Iterator[Dict]:
    """RSS → 個別ページ の2段階で物件情報を取得"""
    for pref_code in PREF_CODES:
        for type_code, _ in BUILDING_TYPES:
            urls = _fetch_rss_urls(pref_code, type_code)
            for url in urls:
                prop = _fetch_detail(url, pref_code, type_code)
                if prop:
                    yield prop
            time.sleep(random.uniform(1.5, 3.0))
