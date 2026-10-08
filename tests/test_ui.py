"""python3 -m unittest discover tests      The project page and its live demo, in a real browser.

Serves docs/ the way GitHub Pages does and drives headless Chromium with real mouse, keyboard
and touch events. Skipped when no Chromium is installed. Run tools/build_site.py first.
"""
import json
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import cdp  # noqa: E402
import preview  # noqa: E402

HAVE_BROWSER = any(shutil.which(n) for n in cdp.CHROMIUM)
SEED = [[1, 37.9, 51.2], [2, 142.8, 173.5], [3, 269.9, 291.0]]


class Page(unittest.TestCase):
    size, touch = (1280, 800), False

    @classmethod
    def setUpClass(cls):
        if not HAVE_BROWSER:
            raise unittest.SkipTest("no Chromium on the PATH")
        cls.srv = preview.make_server("127.0.0.1", 0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d%s" % (cls.srv.server_address[1], preview.PREFIX)
        cls.b = cdp.Browser(*cls.size, touch=cls.touch)

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        cls.srv.shutdown()
        cls.srv.server_close()

    # ---- helpers
    def demo(self, fresh=True):
        b = self.b
        b.goto(self.base + "demo/")
        b.wait("window.__clipper && document.getElementById('v').readyState >= 1", what="the demo")
        if fresh:
            b.js("document.getElementById('again').click()")
            b.wait("window.__clipper.state().cutlist.clips.length === 3", what="the starting clips")

    def clips(self):
        return json.loads(self.b.js("JSON.stringify(window.__clipper.state().cutlist.clips.map(function(c){return [c.n,c['in'],c.out];}))"))

    def cur(self):
        return self.b.js("window.__clipper.cur()")

    def seek(self, t):
        self.b.js("window.__clipper.seek(%r)" % t)
        self.b.wait("!document.getElementById('v').seeking", what="the seek")

    def show(self, selector):
        self.b.js("document.querySelector(%s).scrollIntoView({block:'center',behavior:'instant'})" % json.dumps(selector))
        time.sleep(0.15)

    def until(self, expression, what):
        self.b.wait(expression, timeout=10, what=what)

    def assert_clean(self):
        self.assertEqual(self.b.errors, [])
        outside = [u for u in self.b.requests if not u.startswith((self.base, "data:", "about:", "blob:"))]
        self.assertEqual(outside, [], "the page asked another site for something")

    def sideways(self):
        return self.b.js("document.documentElement.scrollWidth - window.innerWidth")


class Desktop(Page):
    def test_demo_starts_with_three_clips_and_a_transcript(self):
        self.demo()
        self.assertEqual(self.clips(), SEED)
        self.assertEqual(self.b.js("document.querySelectorAll('#lines .ln').length"), 38)
        self.assertEqual(self.b.js("document.getElementById('ver').textContent"), "v" + (ROOT / "VERSION").read_text().strip())
        self.assertGreater(self.b.js("document.getElementById('v').duration"), 300)

    def test_mark_with_the_button(self):
        self.demo()
        self.seek(60)
        self.b.click("#mark")
        self.assertEqual(self.b.js("document.getElementById('mark').className"), "rec")
        self.seek(66.5)
        self.assertIn("0:06", self.b.js("document.getElementById('mark').textContent"))
        self.b.click("#mark")
        self.until("window.__clipper.state().cutlist.clips.length === 4", "the new clip")
        self.assertEqual(self.clips()[3], [4, 60, 66.5])
        self.assertEqual(self.b.js("document.getElementById('submit').textContent"), "SUBMIT 4 CLIPS")

    def test_mark_with_the_keyboard_and_throw_one_away(self):
        self.demo()
        self.seek(70)
        self.b.key("Enter", vk=13)
        self.seek(72)
        self.b.key("Escape", vk=27)                                   # thrown away
        self.assertIsNone(self.b.js("window.__clipper.marking()"))
        self.seek(80)
        self.b.key("Enter", vk=13)
        self.seek(75)                                                # marked backwards
        self.b.key("Enter", vk=13)
        self.until("window.__clipper.state().cutlist.clips.length === 4", "the new clip")
        self.assertEqual(self.clips()[3], [4, 75, 80])

    def test_space_plays_even_after_a_button_was_clicked(self):
        self.demo()
        self.seek(20)
        self.b.click('[data-rate="2"]')
        self.b.key(" ", code="Space", vk=32)
        self.until("!document.getElementById('v').paused", "playback")
        self.assertEqual(self.b.js("document.getElementById('v').playbackRate"), 2)
        self.until("window.__clipper.cur() > 20.3", "the tape to move")
        self.b.key(" ", code="Space", vk=32)
        self.until("document.getElementById('v').paused", "pause")
        self.assertIsNone(self.b.js("window.__clipper.marking()"))    # Space did not press a button
        self.assertGreater(self.cur(), 20)

    def test_drag_a_clip_to_reorder(self):
        self.demo()
        at = self.cur()
        x0, y0 = self.b.centre('.clip[data-n="3"] s')
        x1, y1 = self.b.centre('.clip[data-n="1"] s', fy=0.1)
        self.b.drag(x0, y0, x1, y1)
        self.until("window.__clipper.state().cutlist.clips[0]['in'] === 269.9", "the new order")
        self.assertEqual([c[1] for c in self.clips()], [269.9, 37.9, 142.8])
        self.assertEqual(self.cur(), at, "the drag must not also count as a click on the row")

        x0, y0 = self.b.centre('.clip[data-n="1"] s')                # and down into last place
        r = self.b.rect("#clips")
        self.b.drag(x0, y0, x0, r["y"] + r["h"] - 10)
        self.until("window.__clipper.state().cutlist.clips[2]['in'] === 269.9", "the drop into last place")
        self.assertEqual([c[1] for c in self.clips()], [37.9, 142.8, 269.9])

    def test_click_a_clip_to_jump_and_play_just_it(self):
        self.demo()
        self.b.click('.clip[data-n="2"] s')
        self.until("Math.abs(window.__clipper.cur() - 142.8) < 0.1", "the jump")
        self.b.click('.clip[data-n="1"] [data-play]')
        self.until("!document.getElementById('v').paused", "playback of one clip")
        self.assertAlmostEqual(self.cur(), 37.9, delta=2)
        self.b.js("document.getElementById('v').pause()")

    def test_scrub_both_timelines(self):
        self.demo()
        d = self.b.js("window.__clipper.state().cutlist.duration")
        x0, y = self.b.centre("#ov", fx=0.1)
        x1, _ = self.b.centre("#ov", fx=0.5)
        self.b.drag(x0, y, x1, y)
        self.assertAlmostEqual(self.cur(), d * 0.5, delta=d * 0.01)
        self.until("Math.abs(document.getElementById('v').currentTime - window.__clipper.cur()) < 0.2", "the picture to catch up")

        before = self.cur()                                          # the detail strip shows 40 s
        x0, y = self.b.centre("#det", fx=0.5)
        x1, _ = self.b.centre("#det", fx=0.75)
        self.b.drag(x0, y, x1, y)
        self.assertAlmostEqual(self.cur(), before + 10, delta=0.5)

    def test_delete_and_undo(self):
        self.demo()
        self.b.click('.clip[data-n="2"] [data-del]')
        self.until("window.__clipper.state().cutlist.clips.length === 2", "the delete")
        self.assertEqual([c[1] for c in self.clips()], [37.9, 269.9])
        self.b.key("z", code="KeyZ", vk=90, modifiers=2)
        self.until("window.__clipper.state().cutlist.clips.length === 3", "the undo")
        self.assertEqual(self.clips(), SEED)

    def test_clips_survive_a_reload(self):
        self.demo()
        self.b.click('.clip[data-n="1"] [data-del]')
        self.until("window.__clipper.state().cutlist.clips.length === 2", "the delete")
        self.demo(fresh=False)
        self.assertEqual([c[1] for c in self.clips()], [142.8, 269.9])

    def test_search_the_transcript(self):
        self.demo()
        self.b.click("#q")
        for ch in "launch date":
            self.b.key(ch, code="", text=ch)
        self.until("document.querySelectorAll('#lines .ln').length === 4", "the search")
        self.b.click("#lines .ln")
        self.until("Math.abs(window.__clipper.cur() - 143.01) < 0.1", "the jump to that line")

    def test_submit_is_a_pretend_run(self):
        self.demo()
        self.b.click("#submit")
        self.until("document.getElementById('job').classList.contains('ask')", "the question")
        self.assertIn("Render 3 clips?", self.b.js("document.getElementById('jt').textContent"))
        self.b.click("#go")
        self.until("document.getElementById('job').classList.contains('done')", "the pretend run to finish")
        self.assertIn("Pretend run finished", self.b.js("document.getElementById('jl').textContent"))
        self.b.click('.clip[data-n="3"] [data-del]')                  # the list changed: the notice goes
        self.until("document.getElementById('job').className === ''", "the finished notice to clear")

    def test_project_page(self):
        b = self.b
        b.goto(self.base)
        self.assertEqual(self.sideways(), 0)
        self.assertIn("v" + (ROOT / "VERSION").read_text().strip(), b.js("document.querySelector('footer').textContent"))
        b.js("[].forEach.call(document.images, function(i){i.loading='eager';})")      # do not wait to be scrolled to
        self.until("[].every.call(document.images, function(i){return i.complete && i.naturalWidth > 0;})", "every picture to load")
        self.show("#demo")
        self.until("document.getElementById('demo').contentWindow.__clipper", "the embedded editor")

        self.show("#speed")
        self.assertEqual(b.js("document.getElementById('waitA').textContent"), "6.2")
        b.click("#speed", fx=0.002)
        self.assertEqual((b.js("document.getElementById('mbps').textContent"), b.js("document.getElementById('waitA').textContent")), ("0.5", "25"))
        x0, y = b.centre("#speed", fx=0.002)
        x1, _ = b.centre("#speed", fx=0.998)
        b.drag(x0, y, x1, y)                                          # drag the slider to the far end
        self.assertEqual((b.js("document.getElementById('mbps').textContent"), b.js("document.getElementById('waitA').textContent"),
                          b.js("document.getElementById('waitB').textContent")), ("25", "0.5", "0.1"))
        self.show("#jump")
        b.click("#jump")
        self.assertFalse(b.js("document.getElementById('scrA').classList.contains('shown')"))
        self.until("document.getElementById('scrA').classList.contains('shown') && document.getElementById('scrB').classList.contains('shown')", "both pictures")

    def test_zz_nothing_went_wrong_in_the_browser(self):
        self.assert_clean()


class Phone(Page):
    size, touch = (390, 800), True

    def test_demo_fits_and_works_by_touch(self):
        self.demo()
        self.assertEqual(self.sideways(), 0)
        bar = self.b.rect("#submit")
        self.assertLessEqual(bar["x"] + bar["w"], 390, "SUBMIT must not run off the screen")
        self.seek(60)
        self.b.click("#mark")
        self.seek(66)
        self.b.click("#mark")
        self.until("window.__clipper.state().cutlist.clips.length === 4", "the new clip")
        self.assertEqual(self.clips()[3], [4, 60, 66])

        self.show('.clip[data-n="4"]')                               # finger on the grip: reorder
        x0, y0 = self.b.centre('.clip[data-n="4"] .grip')
        x1, y1 = self.b.centre('.clip[data-n="1"] .grip', fy=0.1)
        self.b.drag(x0, y0, x1, y1)
        self.until("window.__clipper.state().cutlist.clips[0]['in'] === 60", "the new order")

        self.show("#ov")                                             # finger on the strip: scrub
        d = self.b.js("window.__clipper.state().cutlist.duration")
        x0, y = self.b.centre("#ov", fx=0.2)
        x1, _ = self.b.centre("#ov", fx=0.8)
        self.b.drag(x0, y, x1, y)
        self.assertAlmostEqual(self.cur(), d * 0.8, delta=d * 0.02)

    def test_project_page_fits(self):
        self.b.goto(self.base)
        self.assertEqual(self.sideways(), 0)
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('.frame')).display"), "none")
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('.poster')).display"), "block")
        self.b.js("window.scrollTo(0, document.body.scrollHeight)")
        self.assertEqual(self.sideways(), 0)

    def test_zz_nothing_went_wrong_in_the_browser(self):
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
