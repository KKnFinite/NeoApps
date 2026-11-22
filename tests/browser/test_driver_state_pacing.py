"""Two visible driver displays sharing a browser profile and a five-second server floor."""
import time
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright


class DriverStatePacingTest(unittest.TestCase):
    def test_concurrent_displays_and_sort_end(self):
        script = Path('app/static/js/live_revision.js').read_text(encoding='utf-8')
        with sync_playwright() as pw:
            for name in ('chromium', 'webkit'):
                with self.subTest(engine=name):
                    browser = getattr(pw, name).launch()
                    try:
                        for pacing in (False, True):
                            with browser.new_context() as context:
                                starts = []
                                def respond(route):
                                    if route.request.url.endswith('/state'):
                                        now = time.monotonic()
                                        status = 429 if starts and now - starts[-1] < 5 else 200
                                        starts.append(now)
                                        route.fulfill(status=status, body='state')
                                    else:
                                        route.fulfill(content_type='text/html', body='<meta name="live-poll-scope" content="synthetic">')
                                context.route('http://127.0.0.1:38383/**', respond)
                                tabs = [context.new_page(), context.new_page()]
                                for tab in tabs:
                                    tab.goto('http://127.0.0.1:38383/display')
                                    tab.add_script_tag(content=script)
                                    tab.evaluate("""pace => {window.startRead = () => (pace ?
                                        NeoLiveRevision.pacedState('/state', {}, () => window.openSort) :
                                        NeoLiveRevision.fetch('/state')).then(r=>r.status).catch(e=>e.status || e.name);
                                        window.openSort=true;}""", pacing)
                                for tab in tabs:
                                    tab.evaluate('() => {window.result = startRead();}')
                                statuses = [tab.evaluate('() => window.result') for tab in tabs]
                                self.assertEqual(sorted(statuses), [200, 200] if pacing else [200, 429])
                                self.assertEqual(len(starts), 2)
                                if pacing:
                                    self.assertGreaterEqual(starts[1] - starts[0], 5)
                                    tabs[0].evaluate('() => {window.result=startRead();setTimeout(()=>window.openSort=false,50);}')
                                    self.assertEqual(tabs[0].evaluate('() => window.result'), 'AbortError')
                                    self.assertEqual(len(starts), 2)
                                else:
                                    # Compare the same two successful state deliveries.
                                    retry = tabs[statuses.index(429)]
                                    retry.wait_for_timeout(5200)
                                    self.assertEqual(retry.evaluate('() => startRead()'), 200)
                                    self.assertEqual(len(starts), 3)
                    finally:
                        browser.close()
