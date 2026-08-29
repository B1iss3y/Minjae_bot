from __future__ import annotations

import json
import importlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from db.manager import ActiveSessionError, DatabaseManager
from utils.schedule import KST, build_vote_slots, parse_config_time


class ScheduleTests(unittest.TestCase):
    def test_changed_runtime_modules_import(self):
        for module_name in (
            "main",
            "cogs.vote",
            "cogs.config",
            "cogs.archive",
            "cogs.lottery",
        ):
            self.assertIsNotNone(importlib.import_module(module_name))

    def test_configured_times_and_midnight_are_real_datetimes(self):
        now = datetime(2026, 8, 29, 21, 0, tzinfo=KST)
        slots = build_vote_slots(now, "13:30", "24:00", days=1)

        self.assertEqual(slots[0]["label"], "08/30(일) 낮 13:30")
        self.assertEqual(slots[0]["datetime"], "2026-08-30T13:30:00+09:00")
        self.assertEqual(slots[1]["label"], "08/30(일) 밤 24:00")
        self.assertEqual(slots[1]["datetime"], "2026-08-31T00:00:00+09:00")

        with self.assertRaises(ValueError):
            parse_config_time("24:01")


class DatabaseVoteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "vote-test.db")
        self.db = DatabaseManager(self.db_path)
        await self.db.init()
        self.now = datetime(2026, 8, 29, 21, 0, tzinfo=KST)
        self.slots = build_vote_slots(self.now, "14:00", "24:00", days=1)

    async def asyncTearDown(self):
        await self.db.close()
        self.temp_dir.cleanup()

    async def _create_session(self, guild_id: int = 100) -> dict:
        return await self.db.create_vote_session(
            guild_id=guild_id,
            channel_id=200,
            start_date=self.now.isoformat(),
            deadline_at=(self.now + timedelta(hours=24)).isoformat(),
            slots=self.slots,
        )

    async def test_cancelled_vote_does_not_consume_session_number(self):
        first = await self._create_session()
        self.assertEqual(first["session_number"], 1)
        with self.assertRaises(ActiveSessionError):
            await self._create_session()

        await self.db.cancel_vote_session(first["id"])
        second = await self._create_session()
        self.assertEqual(second["session_number"], 1)

        await self.db.update_vote_session_message(second["id"], 300)
        self.assertTrue(await self.db.claim_vote_close(second["id"]))
        self.assertFalse(await self.db.claim_vote_close(second["id"]))
        await self.db.save_vote_winner(
            second["id"], self.slots[0]["label"], self.slots[0]["datetime"]
        )
        await self.db.finish_vote_close(second["id"], 301)
        await self.db.set_attendance_response(
            second["id"], 400, True, self.slots[0]["label"]
        )
        self.assertTrue(await self.db.confirm_vote_session(second["id"]))

        completed = await self.db.complete_session(second["id"], 500, "테스트 게임")
        self.assertIsNotNone(completed)
        self.assertIsNone(
            await self.db.complete_session(second["id"], 500, "테스트 게임")
        )
        history = await self.db.get_meeting_history(100)
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0]["confirmed_datetime"], self.slots[0]["datetime"]
        )

        third = await self._create_session()
        self.assertEqual(third["session_number"], 2)

    async def test_attendance_is_unique_and_absence_is_persisted(self):
        session = await self._create_session(101)
        await self.db.set_attendance_response(
            session["id"], 401, True, self.slots[0]["label"]
        )
        await self.db.set_attendance_response(
            session["id"], 401, True, self.slots[0]["label"]
        )
        self.assertEqual(len(await self.db.get_attendees(session["id"])), 1)
        self.assertEqual(
            await self.db.get_attendance_response_count(session["id"]), 1
        )

        await self.db.set_attendance_response(
            session["id"], 401, False, self.slots[0]["label"]
        )
        self.assertEqual(await self.db.get_attendees(session["id"]), [])
        self.assertEqual(
            await self.db.get_attendance_response_count(session["id"]), 1
        )

    async def test_restart_reads_vote_slots_responses_and_deadline(self):
        session = await self._create_session(102)
        await self.db.update_vote_session_message(session["id"], 302)
        await self.db.upsert_vote_response(
            session["id"], 402, [self.slots[1]["label"]], is_completed=True
        )
        await self.db.close()

        self.db = DatabaseManager(self.db_path)
        await self.db.init()
        recoverable = await self.db.get_recoverable_vote_sessions()
        restored = next(item for item in recoverable if item["id"] == session["id"])
        self.assertEqual(restored["message_id"], 302)
        self.assertEqual(restored["deadline_at"], session["deadline_at"])
        self.assertEqual(json.loads(restored["slots"]), self.slots)
        responses = await self.db.get_vote_responses(session["id"])
        self.assertEqual(json.loads(responses[0]["selected_slots"]), [self.slots[1]["label"]])

    async def test_vote_and_attendance_views_are_persistent(self):
        from cogs.vote import AttendanceView, VoteView

        class FakeBot:
            db = None

        vote_view = VoteView(
            FakeBot(),
            guild_id=103,
            slots=self.slots,
            deadline_at=self.now + timedelta(hours=1),
            session_id=1,
        )
        attendance_view = AttendanceView(FakeBot(), 1, self.slots[0]["label"], 103)
        self.assertTrue(vote_view.is_persistent())
        self.assertTrue(attendance_view.is_persistent())


class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_attendees_are_deduplicated_and_constrained(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "legacy.db")
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE session_attendees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    confirmed_slot TEXT
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO session_attendees
                    (session_id, user_id, confirmed_slot)
                VALUES (?, ?, ?)
                """,
                [(1, 10, "낮"), (1, 10, "낮")],
            )
            connection.commit()
            connection.close()

            db = DatabaseManager(db_path)
            await db.init()
            self.assertEqual(len(await db.get_attendees(1)), 1)
            await db.add_attendee(1, 10, "밤")
            attendees = await db.get_attendees(1)
            self.assertEqual(len(attendees), 1)
            self.assertEqual(attendees[0]["confirmed_slot"], "밤")
            await db.close()


if __name__ == "__main__":
    unittest.main()
