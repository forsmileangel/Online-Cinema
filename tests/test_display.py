import unittest
from unittest.mock import patch

from backend import display, main
from backend.models import Card, Episode, HomePayload, HomeRow, Listing, VideoDetail


def card(title, source="hongguo", original=None):
    return Card(id=title, title=title, cover="", source=source, original_title=original)


class DisplayTitleTests(unittest.TestCase):
    def test_simplified_titles_show_in_traditional_for_listed_sources_only(self):
        self.assertEqual(display.title("hongguo", "好雨知时节"), "好雨知時節")
        self.assertEqual(display.title("91md", "在邮轮上到底要打几次"), "在郵輪上到底要打幾次")
        self.assertEqual(display.title("chinaq", "好雨知时节"), "好雨知时节")  # Not a Simplified source.
        self.assertEqual(display.title("hongguo", "好雨知時節"), "好雨知時節")
        self.assertEqual(display.title("hongguo", ""), "")
        self.assertIsNone(display.title("hongguo", None))

    def test_japanese_and_korean_titles_are_left_as_written(self):
        for text in ("彼氏にスク水通学を命じられ", "로또 1등도 출근합니다 东京", "MIBB-089 ドスケベ"):
            self.assertEqual(display.title("mmov", text), text)

    def test_listing_home_and_video_convert_every_title_field(self):
        listing = display.listing("hongguo", Listing(items=[card("冲吧出租车大叔", original="冲吧出租车大叔"), card("Other", "chinaq")],
                                                     page=1, has_next=False))
        self.assertEqual([c.title for c in listing.items], ["衝吧出租車大叔", "Other"])
        self.assertEqual(listing.items[0].original_title, "衝吧出租車大叔")
        home = display.home("hongguo", HomePayload(continue_watching=[card("时光出租车")], mirror="",
                                                   rows=[HomeRow(kind="a", title="古装", items=[card("玄武生花")])]))
        self.assertEqual((home.continue_watching[0].title, home.rows[0].items[0].title), ("時光出租車", "玄武生花"))
        detail = display.video("hongguo", VideoDetail(id="1", source="hongguo", title="八零辣妻：失忆老公求转正", cover="", playlist="",
                                                      episodes=[Episode(id="1", title="第1集")], related=[card("扶摇成凰")]))
        self.assertEqual((detail.title, detail.related[0].title), ("八零辣妻：失憶老公求轉正", "扶搖成凰"))

    def test_saved_rows_use_their_own_source(self):
        rows = display.rows(None, [dict(title="好雨知时节", source="hongguo"), dict(title="好雨知时节", source="gimy"),
                                   dict(title="某劇", episode_title="第1集", source="duanju51")])
        self.assertEqual([r["title"] for r in rows], ["好雨知時節", "好雨知时节", "某劇"])

    def test_endpoints_convert_what_they_return(self):
        listing = Listing(items=[card("时光出租车")], page=1, has_next=False)
        with patch.object(main.sites, "get") as get, patch.object(main.terms, "search", return_value=listing):
            self.assertEqual(main.search("出租车", 1, "hongguo").items[0].title, "時光出租車")
        with patch.object(main.db, "list_favorites", return_value=[dict(video_id="1", title="时光出租车", source="hongguo", cover="")]):
            self.assertEqual(main.favorites()["items"][0]["title"], "時光出租車")


if __name__ == "__main__":
    unittest.main()
