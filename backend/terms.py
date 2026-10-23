"""Taiwan and mainland wording for the same thing (計程車 / 出租车) in searches.

OpenCC's tw2sp / s2twp phrase tables cover mostly computing words (軟體、簡訊);
the pairs below add everyday and drama words that titles actually use.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import zh
from .models import Listing

# (Taiwan, mainland). Both sides are matched in simplified script.
PAIRS = (
    ("計程車", "出租车"), ("機車", "摩托车"), ("捷運", "地铁"), ("公車", "公交车"), ("腳踏車", "自行车"),
    ("便利商店", "便利店"), ("外送", "外卖"), ("宅配", "快递"), ("宵夜", "夜宵"), ("速食", "快餐"),
    ("泡麵", "方便面"), ("洋芋片", "薯片"), ("優格", "酸奶"), ("番茄", "西红柿"), ("鳳梨", "菠萝"),
    ("奇異果", "猕猴桃"), ("馬鈴薯", "土豆"), ("鮪魚", "金枪鱼"), ("高麗菜", "卷心菜"), ("冷氣", "空调"),
    ("寶特瓶", "塑料瓶"), ("影集", "剧集"), ("卡通", "动画片"), ("陸劇", "国产剧"), ("電玩", "电子游戏"),
    ("保全", "保安"), ("黑道", "黑社会"), ("霸凌", "欺凌"), ("品質", "质量"), ("雷射", "激光"),
    ("國中", "初中"), ("國小", "小学"), ("網美", "网红"), ("部落客", "博主"), ("直播主", "主播"),
    ("人工智慧", "人工智能"), ("智慧型手機", "智能手机"), ("數位", "数码"), ("程式", "程序"),
    ("賓士", "奔驰"), ("藍寶堅尼", "兰博基尼"), ("杜拜", "迪拜"), ("雪梨", "悉尼"), ("紐西蘭", "新西兰"),
    ("義大利", "意大利"), ("寮國", "老挝"), ("北韓", "朝鲜"), ("南韓", "韩国"),
)


@lru_cache(maxsize=1)
def _tables():
    to_mainland = {zh.to_simplified(tw): cn for tw, cn in PAIRS}
    to_taiwan = {cn: zh.to_simplified(tw) for tw, cn in PAIRS}

    def pattern(words):
        # Longest first, so 智慧型手機 wins over a shorter overlapping word.
        return re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)))
    return (pattern(to_mainland), to_mainland), (pattern(to_taiwan), to_taiwan)


def _swap(text: str, table) -> str:
    pattern, words = table
    return pattern.sub(lambda m: words[m[0]], text)


def phrase_variants(query: str) -> list[str]:
    """The query in the other side's wording, when that changes more than the script."""
    mainland_table, taiwan_table = _tables()
    mainland = _swap(zh.convert_phrases(query, "tw2sp"), mainland_table)
    taiwan = zh.convert_phrases(_swap(zh.to_simplified(query), taiwan_table), "s2twp")
    own = zh.search_key(query)
    found = []
    for variant in (mainland, taiwan):
        if variant and zh.search_key(variant) != own and zh.search_key(variant) not in map(zh.search_key, found):
            found.append(variant)
    return found


EXTRA_LIMIT = 36


def search(site, query: str, page: int = 1) -> Listing:
    """Search as typed; also try the other side's wording for the same words.

    With no results, the reworded search answers instead; on the first page its
    results that are not already listed are added after the original ones.
    """
    listing = site.search(query, page=page)
    if listing.items and page > 1:
        return listing
    variants = phrase_variants(query)
    for variant in variants:
        try:
            other = site.search(variant, page=page if not listing.items else 1)
        except Exception:
            continue  # The query as typed already answered; a reworded failure adds nothing.
        # Loose site search matches parts (出租 for 出租车); keep titles with the whole wording.
        key = zh.search_key(variant)
        matched = [item for item in other.items if key in zh.search_key(item.title)]
        if not matched:
            continue
        if not listing.items:
            note = f"找不到「{query}」，改用「{variant}」的搜尋結果。"
            return other.model_copy(update={"items": matched, "notice": " ".join(filter(None, [note, other.notice]))})
        seen = {(item.source, item.id) for item in listing.items}
        extra = [item for item in matched if (item.source, item.id) not in seen][:EXTRA_LIMIT]
        if extra:
            note = f"也加入「{variant}」的搜尋結果。"
            return listing.model_copy(update={"items": listing.items + extra,
                                              "notice": " ".join(filter(None, [listing.notice, note]))})
    return listing
