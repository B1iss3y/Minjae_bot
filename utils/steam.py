"""
Steam Storefront API 유틸리티
- URL → AppID 파싱 (정규식)
- 게임 검색 (storesearch 엔드포인트)
- 게임 상세 조회 (appdetails 엔드포인트)
"""

import re
import aiohttp
from typing import Optional

# Steam URL 패턴: 다양한 형태의 스팀 상점 URL에서 AppID 추출
_STEAM_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:store\.)?steampowered\.com/app/(\d+)"
)

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


def parse_app_id(text: str) -> Optional[int]:
    """
    Steam 상점 URL에서 AppID를 추출합니다.
    URL이 아닌 경우 None을 반환합니다.
    """
    match = _STEAM_URL_PATTERN.search(text)
    if match:
        return int(match.group(1))
    return None


async def search_games(query: str, max_results: int = 3) -> list[dict]:
    """
    Steam Storefront API로 게임을 검색합니다.
    상위 max_results 개의 결과를 반환합니다.

    Returns:
        [{"app_id": int, "name": str, "icon": str}, ...]
    """
    params = {
        "term": query,
        "l": "koreana",
        "cc": "KR",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                STORE_SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        items = data.get("items", [])
        results = []
        for item in items[:max_results]:
            results.append(
                {
                    "app_id": item.get("id"),
                    "name": item.get("name", "Unknown"),
                    "icon": item.get("tiny_image", ""),
                }
            )
        return results

    except (aiohttp.ClientError, TimeoutError, Exception):
        return []


async def get_app_details(app_id: int) -> Optional[dict]:
    """
    Steam AppID로 게임 상세 정보를 조회합니다.

    Returns:
        {
            "app_id": int,
            "title": str,
            "tags": [str, ...],
            "platforms": {"windows": bool, "mac": bool, "linux": bool},
            "is_free": bool,
            "price_overview": {...} | None,
            "header_image": str,
            "store_url": str,
        }
    """
    params = {"appids": str(app_id), "l": "koreana", "cc": "KR"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                APP_DETAILS_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        app_data = data.get(str(app_id), {})
        if not app_data.get("success"):
            return None

        info = app_data["data"]

        # 태그(카테고리) 추출
        tags = [g.get("description", "") for g in info.get("genres", [])]

        # 플랫폼 정보
        platforms = info.get("platforms", {"windows": False, "mac": False, "linux": False})

        # 가격 정보
        is_free = info.get("is_free", False)
        price_overview = info.get("price_overview")
        # 할인 판별: price_overview가 있고 discount_percent > 0이면 할인 중
        if price_overview and price_overview.get("discount_percent", 0) > 0:
            price_overview["on_sale"] = True
        elif price_overview:
            price_overview["on_sale"] = False

        return {
            "app_id": app_id,
            "title": info.get("name", "Unknown"),
            "tags": tags,
            "platforms": platforms,
            "is_free": is_free,
            "price_overview": price_overview,
            "header_image": info.get("header_image", ""),
            "store_url": f"https://store.steampowered.com/app/{app_id}",
        }

    except (aiohttp.ClientError, TimeoutError, Exception):
        return None


def is_steam_url(text: str) -> bool:
    """입력이 Steam URL인지 판별합니다."""
    return bool(_STEAM_URL_PATTERN.search(text))


def format_price(price_overview: dict | None, is_free: bool) -> str:
    """가격 정보를 사람이 읽기 좋은 형태로 변환합니다."""
    if is_free:
        return "🆓 무료 플레이"
    if not price_overview:
        return "가격 정보 없음"
    final = price_overview.get("final_formatted", "")
    discount = price_overview.get("discount_percent", 0)
    if discount > 0:
        original = price_overview.get("initial_formatted", "")
        return f"~~{original}~~ → **{final}** (-{discount}%)"
    return final
