"""
DatabaseManager: aiosqlite 기반 비동기 DB 관리자
모든 테이블 DDL 및 CRUD 메서드를 제공합니다.
"""

import asyncio
import aiosqlite
import json
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DB_PATH = "minjae_bot.db"


class ActiveSessionError(RuntimeError):
    """서버에 아직 끝나지 않은 모임 흐름이 있을 때 발생한다."""


def _now_iso() -> str:
    """현재 KST 시각을 ISO 문자열로 반환"""
    return datetime.now(KST).isoformat()


class DatabaseManager:
    """싱글톤 패턴의 비동기 DB 매니저"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self):
        """DB 연결 및 테이블 초기화"""
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()

    async def close(self):
        """DB 연결 종료"""
        if self._db:
            await self._db.close()
            self._db = None

    # ──────────────────────────── DDL ────────────────────────────

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS server_config (
                guild_id    INTEGER PRIMARY KEY,
                day_time    TEXT    NOT NULL DEFAULT '14:00',
                night_time  TEXT    NOT NULL DEFAULT '24:00',
                vote_deadline_hours INTEGER NOT NULL DEFAULT 24,
                session_number      INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id          INTEGER NOT NULL,
                guild_id         INTEGER NOT NULL,
                nickname         TEXT,
                os_type          TEXT    NOT NULL DEFAULT 'Windows',
                steam_friend_code TEXT,
                registered_at    TEXT,
                PRIMARY KEY (user_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS wishlist (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                app_id          INTEGER NOT NULL,
                title           TEXT    NOT NULL,
                tags            TEXT,
                platforms       TEXT,
                is_free         INTEGER NOT NULL DEFAULT 0,
                price_overview  TEXT,
                status          TEXT    NOT NULL DEFAULT '미플레이',
                added_by        INTEGER,
                added_at        TEXT,
                header_image    TEXT,
                UNIQUE(guild_id, app_id)
            );

            CREATE TABLE IF NOT EXISTS vote_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                session_number  INTEGER NOT NULL,
                channel_id      INTEGER,
                message_id      INTEGER,
                start_date      TEXT,
                status          TEXT    NOT NULL DEFAULT '진행중',
                deadline_at     TEXT,
                created_at      TEXT,
                slots           TEXT,
                winning_slot    TEXT,
                confirmed_datetime TEXT,
                attendance_message_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS vote_responses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                selected_slots  TEXT,
                is_completed    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(session_id, user_id),
                FOREIGN KEY (session_id) REFERENCES vote_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS session_attendees (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                confirmed_slot  TEXT,
                UNIQUE(session_id, user_id),
                FOREIGN KEY (session_id) REFERENCES vote_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS attendance_responses (
                session_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                is_attending    INTEGER NOT NULL,
                responded_at    TEXT NOT NULL,
                PRIMARY KEY (session_id, user_id),
                FOREIGN KEY (session_id) REFERENCES vote_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS meeting_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id            INTEGER NOT NULL,
                session_number      INTEGER NOT NULL,
                confirmed_datetime  TEXT,
                attendees           TEXT,
                game_app_id         INTEGER,
                game_title          TEXT,
                completed_at        TEXT
            );
        """)
        await self._migrate_schema()
        await self._db.commit()

    async def _migrate_schema(self) -> None:
        """기존 DB를 데이터 손실 없이 현재 스키마로 올린다."""

        async with self._db.execute("PRAGMA table_info(vote_sessions)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        additions = {
            "slots": "TEXT",
            "winning_slot": "TEXT",
            "confirmed_datetime": "TEXT",
            "attendance_message_id": "INTEGER",
        }
        for name, data_type in additions.items():
            if name not in columns:
                await self._db.execute(
                    f"ALTER TABLE vote_sessions ADD COLUMN {name} {data_type}"
                )

        # INSERT OR IGNORE가 실제로 중복을 막도록 레거시 중복을 먼저 정리한다.
        await self._db.execute(
            """
            DELETE FROM session_attendees
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM session_attendees
                GROUP BY session_id, user_id
            )
            """
        )
        await self._db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_session_attendees_session_user
            ON session_attendees(session_id, user_id)
            """
        )

    # ──────────────────────── Server Config ────────────────────────

    async def get_server_config(self, guild_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM server_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_server_config(
        self, guild_id: int, day_time: str, night_time: str, vote_deadline_hours: int
    ) -> dict:
        await self._db.execute(
            """
            INSERT INTO server_config (guild_id, day_time, night_time, vote_deadline_hours)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET day_time=excluded.day_time,
                          night_time=excluded.night_time,
                          vote_deadline_hours=excluded.vote_deadline_hours
            """,
            (guild_id, day_time, night_time, vote_deadline_hours),
        )
        await self._db.commit()
        return await self.get_server_config(guild_id)

    async def get_or_create_config(self, guild_id: int) -> dict:
        """설정이 없으면 기본값으로 생성 후 반환"""
        cfg = await self.get_server_config(guild_id)
        if cfg is None:
            return await self.upsert_server_config(guild_id, "14:00", "24:00", 24)
        return cfg

    async def increment_session_number(self, guild_id: int) -> int:
        cfg = await self.get_or_create_config(guild_id)
        new_num = cfg["session_number"] + 1
        await self._db.execute(
            "UPDATE server_config SET session_number = ? WHERE guild_id = ?",
            (new_num, guild_id),
        )
        await self._db.commit()
        return new_num

    # ──────────────────────── Users ────────────────────────

    async def upsert_user(
        self,
        user_id: int,
        guild_id: int,
        nickname: str,
        os_type: str,
        steam_friend_code: str,
    ) -> dict:
        await self._db.execute(
            """
            INSERT INTO users (user_id, guild_id, nickname, os_type, steam_friend_code, registered_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id)
            DO UPDATE SET nickname=excluded.nickname,
                          os_type=excluded.os_type,
                          steam_friend_code=excluded.steam_friend_code,
                          registered_at=excluded.registered_at
            """,
            (user_id, guild_id, nickname, os_type, steam_friend_code, _now_iso()),
        )
        await self._db.commit()
        return await self.get_user(user_id, guild_id)

    async def get_user(self, user_id: int, guild_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_users(self, guild_id: int) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM users WHERE guild_id = ?", (guild_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ──────────────────────── Wishlist ────────────────────────

    async def add_wishlist_game(
        self,
        guild_id: int,
        app_id: int,
        title: str,
        tags: list | None,
        platforms: dict | None,
        is_free: bool,
        price_overview: dict | None,
        added_by: int,
        header_image: str | None,
    ) -> dict:
        await self._db.execute(
            """
            INSERT INTO wishlist
                (guild_id, app_id, title, tags, platforms, is_free, price_overview,
                 status, added_by, added_at, header_image)
            VALUES (?, ?, ?, ?, ?, ?, ?, '미플레이', ?, ?, ?)
            ON CONFLICT(guild_id, app_id)
            DO UPDATE SET title=excluded.title, tags=excluded.tags,
                          platforms=excluded.platforms, is_free=excluded.is_free,
                          price_overview=excluded.price_overview,
                          header_image=excluded.header_image
            """,
            (
                guild_id,
                app_id,
                title,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(platforms or {}, ensure_ascii=False),
                int(is_free),
                json.dumps(price_overview or {}, ensure_ascii=False),
                added_by,
                _now_iso(),
                header_image,
            ),
        )
        await self._db.commit()
        return await self.get_wishlist_game(guild_id, app_id)

    async def get_wishlist_game(self, guild_id: int, app_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM wishlist WHERE guild_id = ? AND app_id = ?",
            (guild_id, app_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_wishlist(
        self, guild_id: int, status_filter: str | None = None
    ) -> list[dict]:
        if status_filter:
            sql = "SELECT * FROM wishlist WHERE guild_id = ? AND status = ? ORDER BY added_at DESC"
            params = (guild_id, status_filter)
        else:
            sql = "SELECT * FROM wishlist WHERE guild_id = ? ORDER BY added_at DESC"
            params = (guild_id,)
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_eligible_games(self, guild_id: int) -> list[dict]:
        """추첨 풀: 상태가 '완료'가 아닌 게임들 (미플레이만 대상)"""
        async with self._db.execute(
            "SELECT * FROM wishlist WHERE guild_id = ? AND status = '미플레이' ORDER BY added_at",
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_game_status(
        self, guild_id: int, app_id: int, status: str
    ) -> None:
        await self._db.execute(
            "UPDATE wishlist SET status = ? WHERE guild_id = ? AND app_id = ?",
            (status, guild_id, app_id),
        )
        await self._db.commit()

    # ──────────────────────── Vote Sessions ────────────────────────

    async def create_vote_session(
        self,
        guild_id: int,
        channel_id: int,
        start_date: str,
        deadline_at: str,
        slots: list[dict[str, str]],
    ) -> dict:
        """끝나지 않은 흐름이 없을 때 다음 실제 회차로 투표를 만든다.

        회차는 카운터를 두 번 올리는 대신 완료된 기록의 다음 번호를 사용한다.
        취소된 투표는 기록에 들어가지 않으므로 번호도 소비하지 않는다.
        """

        unresolved = ("진행중", "마감처리", "마감", "확정")
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                await self._db.execute(
                    "INSERT OR IGNORE INTO server_config (guild_id) VALUES (?)",
                    (guild_id,),
                )
                placeholders = ",".join("?" for _ in unresolved)
                async with self._db.execute(
                    f"""
                    SELECT id FROM vote_sessions
                    WHERE guild_id = ? AND status IN ({placeholders})
                    LIMIT 1
                    """,
                    (guild_id, *unresolved),
                ) as cur:
                    if await cur.fetchone():
                        raise ActiveSessionError(
                            "아직 끝나지 않은 투표 또는 모임이 있습니다."
                        )

                async with self._db.execute(
                    """
                    SELECT COALESCE(MAX(session_number), 0) + 1
                    FROM meeting_history
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                ) as cur:
                    session_number = (await cur.fetchone())[0]

                cur = await self._db.execute(
                    """
                    INSERT INTO vote_sessions
                        (guild_id, session_number, channel_id, message_id,
                         start_date, status, deadline_at, created_at, slots)
                    VALUES (?, ?, ?, NULL, ?, '진행중', ?, ?, ?)
                    """,
                    (
                        guild_id,
                        session_number,
                        channel_id,
                        start_date,
                        deadline_at,
                        _now_iso(),
                        json.dumps(slots, ensure_ascii=False),
                    ),
                )
                session_id = cur.lastrowid
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise
        return await self.get_vote_session(session_id)

    async def get_active_vote_session(self, guild_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM vote_sessions WHERE guild_id = ? AND status = '진행중' ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_recoverable_vote_sessions(self) -> list[dict]:
        """재시작 후 View 또는 마감 작업을 되살려야 하는 세션 목록."""

        async with self._db.execute(
            """
            SELECT * FROM vote_sessions
            WHERE status IN ('진행중', '마감처리', '마감')
            ORDER BY id
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def get_vote_session(self, session_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM vote_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_latest_confirmed_session(self, guild_id: int) -> dict | None:
        """가장 최근 확정된 세션"""
        async with self._db.execute(
            "SELECT * FROM vote_sessions WHERE guild_id = ? AND status = '확정' ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_vote_session_status(
        self, session_id: int, status: str
    ) -> None:
        await self._db.execute(
            "UPDATE vote_sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
        await self._db.commit()

    async def update_vote_session_message(
        self, session_id: int, message_id: int
    ) -> None:
        await self._db.execute(
            "UPDATE vote_sessions SET message_id = ? WHERE id = ?",
            (message_id, session_id),
        )
        await self._db.commit()

    async def update_vote_session_slots(
        self, session_id: int, slots: list[dict[str, str]]
    ) -> None:
        """복구 가능한 레거시 투표에 계산된 슬롯을 한 번 백필한다."""

        await self._db.execute(
            "UPDATE vote_sessions SET slots = ? WHERE id = ?",
            (json.dumps(slots, ensure_ascii=False), session_id),
        )
        await self._db.commit()

    async def claim_vote_close(self, session_id: int) -> bool:
        """한 호출만 진행 중 투표의 마감을 소유하게 한다."""

        async with self._write_lock:
            cur = await self._db.execute(
                """
                UPDATE vote_sessions SET status = '마감처리'
                WHERE id = ? AND status = '진행중'
                """,
                (session_id,),
            )
            await self._db.commit()
            return cur.rowcount == 1

    async def save_vote_winner(
        self, session_id: int, winning_slot: str, confirmed_datetime: str
    ) -> None:
        await self._db.execute(
            """
            UPDATE vote_sessions
            SET winning_slot = ?, confirmed_datetime = ?
            WHERE id = ? AND status = '마감처리'
            """,
            (winning_slot, confirmed_datetime, session_id),
        )
        await self._db.commit()

    async def finish_vote_close(
        self, session_id: int, attendance_message_id: int
    ) -> None:
        await self._db.execute(
            """
            UPDATE vote_sessions
            SET status = '마감', attendance_message_id = ?
            WHERE id = ? AND status = '마감처리'
            """,
            (attendance_message_id, session_id),
        )
        await self._db.commit()

    async def cancel_vote_session(self, session_id: int) -> None:
        await self._db.execute(
            """
            UPDATE vote_sessions SET status = '취소'
            WHERE id = ? AND status IN ('진행중', '마감처리')
            """,
            (session_id,),
        )
        await self._db.commit()

    # ──────────────────────── Vote Responses ────────────────────────

    async def upsert_vote_response(
        self,
        session_id: int,
        user_id: int,
        selected_slots: list[str],
        is_completed: bool = False,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO vote_responses (session_id, user_id, selected_slots, is_completed)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, user_id)
            DO UPDATE SET selected_slots=excluded.selected_slots,
                          is_completed=excluded.is_completed
            """,
            (session_id, user_id, json.dumps(selected_slots, ensure_ascii=False), int(is_completed)),
        )
        await self._db.commit()

    async def get_vote_responses(self, session_id: int) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM vote_responses WHERE session_id = ?", (session_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_completed_vote_count(self, session_id: int) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM vote_responses WHERE session_id = ? AND is_completed = 1",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0]

    async def get_user_vote(self, session_id: int, user_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM vote_responses WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ──────────────────────── Session Attendees ────────────────────────

    async def add_attendee(
        self, session_id: int, user_id: int, confirmed_slot: str
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO session_attendees (session_id, user_id, confirmed_slot)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, user_id)
            DO UPDATE SET confirmed_slot=excluded.confirmed_slot
            """,
            (session_id, user_id, confirmed_slot),
        )
        await self._db.commit()

    async def remove_attendee(self, session_id: int, user_id: int) -> None:
        await self._db.execute(
            "DELETE FROM session_attendees WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        await self._db.commit()

    async def get_attendees(self, session_id: int) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM session_attendees WHERE session_id = ?", (session_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def set_attendance_response(
        self,
        session_id: int,
        user_id: int,
        is_attending: bool,
        confirmed_slot: str,
    ) -> int:
        """참석/불참 응답과 실제 참석자 목록을 한 트랜잭션으로 맞춘다."""

        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                await self._db.execute(
                    """
                    INSERT INTO attendance_responses
                        (session_id, user_id, is_attending, responded_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, user_id)
                    DO UPDATE SET is_attending=excluded.is_attending,
                                  responded_at=excluded.responded_at
                    """,
                    (session_id, user_id, int(is_attending), _now_iso()),
                )
                if is_attending:
                    await self._db.execute(
                        """
                        INSERT INTO session_attendees
                            (session_id, user_id, confirmed_slot)
                        VALUES (?, ?, ?)
                        ON CONFLICT(session_id, user_id)
                        DO UPDATE SET confirmed_slot=excluded.confirmed_slot
                        """,
                        (session_id, user_id, confirmed_slot),
                    )
                else:
                    await self._db.execute(
                        """
                        DELETE FROM session_attendees
                        WHERE session_id = ? AND user_id = ?
                        """,
                        (session_id, user_id),
                    )
                async with self._db.execute(
                    "SELECT COUNT(*) FROM session_attendees WHERE session_id = ?",
                    (session_id,),
                ) as cur:
                    attendee_count = (await cur.fetchone())[0]
                await self._db.commit()
                return attendee_count
            except Exception:
                await self._db.rollback()
                raise

    async def get_attendance_response_count(self, session_id: int) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM attendance_responses WHERE session_id = ?",
            (session_id,),
        ) as cur:
            return (await cur.fetchone())[0]

    async def confirm_vote_session(self, session_id: int) -> bool:
        """참석자가 있을 때 한 호출만 세션을 확정하게 한다."""

        async with self._write_lock:
            cur = await self._db.execute(
                """
                UPDATE vote_sessions SET status = '확정'
                WHERE id = ? AND status = '마감'
                  AND EXISTS (
                      SELECT 1 FROM session_attendees
                      WHERE session_attendees.session_id = vote_sessions.id
                  )
                """,
                (session_id,),
            )
            await self._db.commit()
            return cur.rowcount == 1

    # ──────────────────────── Meeting History ────────────────────────

    async def add_meeting_history(
        self,
        guild_id: int,
        session_number: int,
        confirmed_datetime: str,
        attendees: list[int],
        game_app_id: int | None,
        game_title: str | None,
    ) -> int:
        cur = await self._db.execute(
            """
            INSERT INTO meeting_history
                (guild_id, session_number, confirmed_datetime, attendees,
                 game_app_id, game_title)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                session_number,
                confirmed_datetime,
                json.dumps(attendees),
                game_app_id,
                game_title,
            ),
        )
        await self._db.commit()
        return cur.lastrowid

    async def update_meeting_completed(self, history_id: int) -> None:
        await self._db.execute(
            "UPDATE meeting_history SET completed_at = ? WHERE id = ?",
            (_now_iso(), history_id),
        )
        await self._db.commit()

    async def complete_session(
        self,
        session_id: int,
        game_app_id: int,
        game_title: str,
    ) -> dict | None:
        """확정 세션을 정확히 한 번 완료 기록으로 전환한다."""

        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                async with self._db.execute(
                    "SELECT * FROM vote_sessions WHERE id = ?", (session_id,)
                ) as cur:
                    row = await cur.fetchone()
                if row is None or row["status"] != "확정":
                    await self._db.rollback()
                    return None

                session = dict(row)
                async with self._db.execute(
                    """
                    SELECT user_id FROM session_attendees
                    WHERE session_id = ? ORDER BY id
                    """,
                    (session_id,),
                ) as cur:
                    attendee_ids = [item[0] for item in await cur.fetchall()]

                async with self._db.execute(
                    """
                    SELECT id FROM meeting_history
                    WHERE guild_id = ? AND session_number = ?
                    ORDER BY id LIMIT 1
                    """,
                    (session["guild_id"], session["session_number"]),
                ) as cur:
                    existing = await cur.fetchone()
                if existing:
                    history_id = existing[0]
                else:
                    cur = await self._db.execute(
                        """
                        INSERT INTO meeting_history
                            (guild_id, session_number, confirmed_datetime,
                             attendees, game_app_id, game_title, completed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session["guild_id"],
                            session["session_number"],
                            session["confirmed_datetime"],
                            json.dumps(attendee_ids),
                            game_app_id,
                            game_title,
                            _now_iso(),
                        ),
                    )
                    history_id = cur.lastrowid

                await self._db.execute(
                    """
                    UPDATE wishlist SET status = '완료'
                    WHERE guild_id = ? AND app_id = ?
                    """,
                    (session["guild_id"], game_app_id),
                )
                await self._db.execute(
                    "UPDATE vote_sessions SET status = '완료' WHERE id = ?",
                    (session_id,),
                )
                await self._db.commit()
                return {
                    "history_id": history_id,
                    "session": session,
                    "attendee_ids": attendee_ids,
                }
            except Exception:
                await self._db.rollback()
                raise

    async def get_meeting_history(
        self, guild_id: int, session_number: int | None = None
    ) -> list[dict]:
        if session_number:
            sql = "SELECT * FROM meeting_history WHERE guild_id = ? AND session_number = ?"
            params = (guild_id, session_number)
        else:
            sql = "SELECT * FROM meeting_history WHERE guild_id = ? ORDER BY session_number DESC"
            params = (guild_id,)
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_latest_history(self, guild_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM meeting_history WHERE guild_id = ? ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
