import os
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.database.storage import ChatStorage


class ChatStorageTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / "chat.db"

    def test_reads_ordered_and_isolated_history_after_reopening(self):
        storage = ChatStorage(self.database)
        storage.save_turn("session-a", "我明天要答辩。", "你准备到哪一步了？")
        storage.save_turn("session-b", "Hello", "How can I help?")
        storage.save_turn("session-a", "I'm ready.\nStill nervous.", "我们可以先练习开场。")

        reopened = ChatStorage(self.database)
        self.assertEqual(
            reopened.load_history("session-a"),
            [
                {"role": "user", "content": "我明天要答辩。"},
                {"role": "assistant", "content": "你准备到哪一步了？"},
                {"role": "user", "content": "I'm ready.\nStill nervous."},
                {"role": "assistant", "content": "我们可以先练习开场。"},
            ],
        )
        self.assertEqual(
            reopened.load_history("session-b"),
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "How can I help?"},
            ],
        )
        self.assertEqual(reopened.load_history("unknown-session"), [])

    def test_uses_relative_database_path_from_environment_and_creates_parent(self):
        configured_database = self.root / "nested" / "chat.db"
        with (
            patch.dict(os.environ, {"DATABASE_PATH": "./nested/chat.db"}),
            patch("backend.database.storage._PROJECT_ROOT", self.root),
        ):
            storage = ChatStorage()
            storage.save_turn("session-a", "你好。", "你好！")

        self.assertTrue(configured_database.is_file())
        self.assertEqual(len(ChatStorage(configured_database).load_history("session-a")), 2)

    def test_rolls_back_entire_turn_when_assistant_insert_fails(self):
        storage = ChatStorage(self.database)
        storage.save_turn("session-a", "历史消息", "历史回复")
        original_history = storage.load_history("session-a")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER reject_assistant_message
                BEFORE INSERT ON chat_messages
                WHEN NEW.role = 'assistant' AND NEW.content = 'blocked'
                BEGIN
                    SELECT RAISE(ABORT, 'assistant insert failed');
                END
                """
            )

        with self.assertRaises(sqlite3.IntegrityError):
            storage.save_turn("session-a", "应当一并回滚", "blocked")

        self.assertEqual(storage.load_history("session-a"), original_history)
        storage.save_turn("session-a", "重试消息", "重试回复")
        self.assertEqual(len(storage.load_history("session-a")), len(original_history) + 2)

    def test_concurrent_turns_are_kept_together(self):
        storage = ChatStorage(self.database)

        def save(index):
            storage.save_turn("session-a", f"question-{index}", f"answer-{index}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(save, range(8)))

        history = storage.load_history("session-a")
        self.assertEqual([message["role"] for message in history], ["user", "assistant"] * 8)
        self.assertCountEqual(
            [(user["content"], reply["content"]) for user, reply in zip(history[::2], history[1::2])],
            [(f"question-{index}", f"answer-{index}") for index in range(8)],
        )
