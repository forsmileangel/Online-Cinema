"""At phone size with a coarse pointer: named controls, 44 px targets, readable accent buttons, no overflow."""
import unittest

from e2e._base import E2ETest
from e2e.cdp import Browser

NAMES = """(() => {
  const bad = [];
  for (const el of document.querySelectorAll('button, a[href], select, input:not([type=hidden]), [role=slider]')) {
    if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') continue;
    const name = (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.textContent || el.getAttribute('title') || el.getAttribute('placeholder') || '').trim()
      || (el.labels && el.labels.length && el.labels[0].textContent.trim());
    if (!name) bad.push(el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 30));
  }
  return bad;
})()"""
SMALL = """(() => {
  const small = [];
  for (const el of document.querySelectorAll('button, select, [role=slider], a.btn, a.chip, .nav a, a.more')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.height < 43.5 || r.width < 43.5) small.push(el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 24) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  return small;
})()"""
CONTRAST = """(() => {
  const lum = (c) => { const [r, g, b] = c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  const out = {};
  for (const sel of ['.btn:not(.alt)', '.chip.on', '.lib-tabs a.active']) {
    const el = document.querySelector(sel); if (!el) continue;
    const cs = getComputedStyle(el); out[sel] = ratio(cs.color, cs.backgroundColor);
  }
  return out;
})()"""


class PhoneTests(E2ETest):
    def test_pages_are_usable_on_a_phone(self):
        async def run():
            async with Browser(self.base, width=390, height=844) as b:
                await b.send("Emulation.setEmulatedMedia", {"features": [{"name": "pointer", "value": "coarse"}, {"name": "hover", "value": "none"}]})
                await b.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
                await self.open(b, "/", 1)
                for path in ("/", "/favorites", "/offline", "/settings"):
                    with self.subTest(path=path):
                        await b.goto(path, 3)
                        await b.wait_for("document.readyState === 'complete' && !!document.querySelector('.topbar')", 30)
                        self.assertEqual(await b.ev(NAMES), [], "controls without an accessible name")
                        self.assertEqual(await b.ev(SMALL), [], "controls under 44 px")
                        for selector, ratio in (await b.ev(CONTRAST)).items():
                            self.assertGreaterEqual(ratio, 4.5, f"{selector} contrast")
                        self.assertEqual(await b.ev("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, "horizontal overflow")
                links = await b.ev("[...document.querySelectorAll('link[rel=icon],link[rel=manifest],link[rel=apple-touch-icon]')].map(e => e.getAttribute('href'))")
                self.assertEqual(sorted(links), ["/apple-touch-icon.png", "/favicon.svg", "/manifest.webmanifest"])
                # Batch title translation is the one POST a listing may send; nothing of the user's is written.
                self.assertEqual([x for x in b.blocked if not x.startswith("POST /api/translate")], [], "a page view must not write anything")
        self.run_async(run())


if __name__ == "__main__":
    unittest.main()
