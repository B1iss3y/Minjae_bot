"""
DatabaseManager: aiosqlite 기반 비동기 DB 관리자
모든 테이블 DDL 및 CRUD 메서드를 제공합니다.
"""

import aiosqlite
import json
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DB_PATH = "minjae_bot.db"


def _now_iso() -> str:
    """현재 KST 시각을 ISO 문자열로 반환"""
    return datetime.now(KST).isoformat()


class DatabaseManager:
    """싱글톤 패턴의 비동기 DB 매니저"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        """DB 연결 및 테이블 초기화"""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()

    async def close(self):
        """DB 연결 종료"""
        if self._db:
            await self._db.close()

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
                created_at      TEXT
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
        await self._db.commit()

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
        session_number: int,
        channel_id: int,
        message_id: int,
        start_date: str,
        deadline_at: str,
    ) -> int:
        cur = await self._db.execute(
            """
            INSERT INTO vote_sessions
                (guild_id, session_number, channel_id, message_id,
                 start_date, status, deadline_at, created_at)
            VALUES (?, ?, ?, ?, ?, '진행중', ?, ?)
            """,
            (guild_id, session_number, channel_id, message_id,
             start_date, deadline_at, _now_iso()),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_active_vote_session(self, guild_id: int) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM vote_sessions WHERE guild_id = ? AND status = '진행중' ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

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
            INSERT OR IGNORE INTO session_attendees (session_id, user_id, confirmed_slot)
            VALUES (?, ?, ?)
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
