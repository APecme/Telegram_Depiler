from __future__ import annotations

import unittest

from backend.app.telegram_links import TelegramLink, parse_telegram_link


class TelegramLinkParsingTests(unittest.TestCase):
    def test_private_channel_link_with_topic_thread(self):
        link = parse_telegram_link("https://t.me/c/3411693582/17748?thread=17724")
        self.assertEqual(link, TelegramLink(chat_ref=-1003411693582, message_id=17748))

    def test_private_channel_link_without_scheme_or_parameters(self):
        self.assertEqual(
            parse_telegram_link("t.me/c/3411693582/17748"),
            TelegramLink(chat_ref=-1003411693582, message_id=17748),
        )

    def test_public_channel_link_keeps_the_username(self):
        self.assertEqual(
            parse_telegram_link("https://t.me/ApecmeChannel/1234?single"),
            TelegramLink(chat_ref="ApecmeChannel", message_id=1234),
        )

    def test_telegram_me_host_and_www_prefix_are_accepted(self):
        self.assertEqual(
            parse_telegram_link("www.telegram.me/ApecmeChannel/1234"),
            TelegramLink(chat_ref="ApecmeChannel", message_id=1234),
        )

    def test_link_inside_a_longer_message_is_found(self):
        self.assertEqual(
            parse_telegram_link("帮我把 https://t.me/c/3411693582/17748?thread=17724 这个下了"),
            TelegramLink(chat_ref=-1003411693582, message_id=17748),
        )

    def test_links_without_a_message_id_are_ignored(self):
        for text in (
            "https://t.me/ApecmeChannel",
            "https://t.me/c/3411693582",
            "https://t.me/joinchat/AAAAAE1234",
            "https://example.com/c/3411693582/17748",
            "3411693582",
            "",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_telegram_link(text))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
