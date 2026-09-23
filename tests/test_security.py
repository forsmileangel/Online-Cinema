import unittest
from unittest.mock import patch

from backend import security
from backend.translate import looks_japanese
from backend.security import (
    UnsafeURL,
    assert_hls_url,
    assert_https_url,
    assert_image_url,
    safe_search_query,
    safe_slug,
    safe_video_id,
)


class SecurityTests(unittest.TestCase):
    def test_looks_japanese(self):
        self.assertTrue(looks_japanese("放課後校内デート。小沢菜穂"))
        self.assertFalse(looks_japanese("DLDSS-533 今天放學後"))

    def test_video_id(self):
        self.assertEqual(safe_video_id("dldss-533"), "dldss-533")
        self.assertEqual(safe_video_id("DLDSS-533"), "DLDSS-533")
        with self.assertRaises(UnsafeURL):
            safe_video_id("../etc/passwd")
        with self.assertRaises(UnsafeURL):
            safe_video_id("a/b")
        with self.assertRaises(UnsafeURL):
            safe_video_id("x" * 90)

    def test_search(self):
        self.assertEqual(safe_search_query("SSIS+巨乳"), "SSIS+巨乳")
        with self.assertRaises(UnsafeURL):
            safe_search_query("<script>")
        with self.assertRaises(UnsafeURL):
            safe_search_query("a/../b")

    def test_slug(self):
        self.assertTrue(safe_slug("小泽菜穗"))
        with self.assertRaises(UnsafeURL):
            safe_slug("../x")
        with self.assertRaises(UnsafeURL):
            safe_slug("a/b")

    def test_reject_non_https_and_private(self):
        with self.assertRaises(UnsafeURL):
            assert_https_url("http://fourhoi.com/x.jpg", {"fourhoi.com"})
        with self.assertRaises(UnsafeURL):
            assert_https_url("https://127.0.0.1/x", {"127.0.0.1"})
        with self.assertRaises(UnsafeURL):
            assert_https_url("https://169.254.169.254/latest", {"169.254.169.254"})
        with self.assertRaises(UnsafeURL):
            assert_image_url("https://surrit.com/x.jpg")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://fourhoi.com/x.m3u8")
        with self.assertRaises(UnsafeURL):
            assert_image_url("https://evil.com/x.jpg")
        with self.assertRaises(UnsafeURL):
            assert_https_url("https://user:pass@fourhoi.com/x.jpg", {"fourhoi.com"})

    def test_drama_cdn_and_cover_hosts_are_allowed(self):
        with patch.object(security, "_assert_not_private"):
            assert_hls_url("https://s2.bfllvip.com/video/a.m3u8")
            assert_image_url("https://img.picbf.com/upload/a.jpg")
            assert_image_url("https://chinaq.fun/img_th/202659534.jpg")
            assert_hls_url("https://ukzy.ukubf3.com/20260914/x/index.m3u8")
            assert_image_url("https://imgs.1777cdn.com/upload/vod/a.jpg")
            assert_hls_url("https://vv.jisuzyv.com/play/x/index.m3u8")
        with self.assertRaises(UnsafeURL):
            assert_image_url("https://evil.com/a.png")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://evil.com/a.m3u8")


if __name__ == "__main__":
    unittest.main()
