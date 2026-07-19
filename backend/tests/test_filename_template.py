from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from backend.app.telegram_worker import _render_filename_template


class FilenameTemplateTests(unittest.TestCase):
    def render(self, template: str, message_text: str = "一段消息文字") -> str:
        return _render_filename_template(
            template,
            task_id=12,
            message=SimpleNamespace(id=34, message=message_text),
            chat_title="示例群聊",
            original_file_name="video.mp4",
            timestamp=1234567890,
            rendered_at=datetime(2026, 7, 20),
        )

    def test_renders_message_text(self) -> None:
        self.assertEqual(
            self.render("{message_id}_{message_text}_{file_name}"),
            "34_一段消息文字_video.mp4",
        )

    def test_limits_any_variable_length(self) -> None:
        self.assertEqual(
            self.render("{chat_title:2}_{message_text:4}_{file_name:5}"),
            "示例_一段消息_video",
        )

    def test_sanitizes_message_text_for_file_names(self) -> None:
        self.assertEqual(
            self.render("{message_text}", "  第一行 / 第二行\n第三行?  "),
            "第一行 _ 第二行 第三行_",
        )

    def test_keeps_unknown_variables_unchanged(self) -> None:
        self.assertEqual(self.render("{unknown}_{year}"), "{unknown}_2026")


if __name__ == "__main__":
    unittest.main()
