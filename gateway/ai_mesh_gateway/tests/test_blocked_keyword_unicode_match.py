import unittest

from main import _blocked_keyword_matches


class BlockedKeywordUnicodeMatchTests(unittest.TestCase):
    def test_blocked_keyword_matches_plain_word_boundary(self):
        self.assertTrue(_blocked_keyword_matches("user noo test", "noo"))

    def test_blocked_keyword_matches_zero_width_obfuscation(self):
        # n + ZWSP + o + ZWSP + o should still match "noo".
        obfuscated = "user n\u200bo\u200bo test"
        self.assertTrue(_blocked_keyword_matches(obfuscated.lower(), "noo"))

    def test_blocked_keyword_matches_fullwidth_homoglyphs(self):
        # Fullwidth letters fold to ASCII under scanner._normalize_unicode.
        obfuscated = "user ｎｏｏ test"
        self.assertTrue(_blocked_keyword_matches(obfuscated.lower(), "noo"))

    def test_blocked_keyword_does_not_match_substring(self):
        self.assertFalse(_blocked_keyword_matches("snoopy", "noo"))
