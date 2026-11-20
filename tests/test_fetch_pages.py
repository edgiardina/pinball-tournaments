import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_pages  # noqa: E402


def canva_like(text: str) -> str:
    # What pinballatthezoo.com (a Canva export) ships: plain JSON objects, but the copy's
    # newlines are escaped twice, so the HTML holds a literal backslash-backslash-n.
    doubled = json.dumps(text)[1:-1].replace("\\", "\\\\")
    return f'<html><head><title>Zoo</title></head><body><script>var d = {{"A":"{doubled}"}};</script></body></html>'


class EmbeddedTextTests(unittest.TestCase):
    def test_recovers_double_escaped_copy_when_html_has_no_text(self):
        out = fetch_pages.html_to_text(canva_like("April 15-17, 2027\nKalamazoo County Expo Center"), "https://x/")
        self.assertIn(fetch_pages.EMBEDDED_HEADER, out)
        self.assertIn("April 15-17, 2027\nKalamazoo County Expo Center", out)

    def test_skips_code_and_keyword_noise(self):
        raw = '<script>a="inclusive event";b="_assets/x.vendor.js";c="function(){return 1;} more words here"</script>'
        self.assertEqual(fetch_pages.embedded_strings(raw), [])

    def test_normal_pages_are_left_alone(self):
        body = " ".join(["word"] * 60)
        raw = f'<p>{body}</p><script>var x = "Registration opens May 1, 2027 at noon";</script>'
        self.assertNotIn(fetch_pages.EMBEDDED_HEADER, fetch_pages.html_to_text(raw, "https://x/"))


if __name__ == "__main__":
    unittest.main()
