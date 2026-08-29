from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from db.manager import ActiveSessionError
from utils.schedule import KST, build_vote_slots, slot_datetime


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(KST) if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _decode_slots(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        slots = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(slots, list):
        return []
    return [
        slot
        for slot in slots
        if isinstance(slot, dict) and slot.get("label") and slot.get("datetime")
    ]


def _decode_selection(raw: str | None) -> set[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    return {item for item in value if isinstance(item, str)}


class AttendanceView(discord.ui.View):
    def __init__(self, bot, session_id: int, confirmed_slot: str, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.session_id = session_id
        self.confirmed_slot = confirmed_slot
        self.guild_id = guild_id
        self.message: discord.Message | None = None
        self._finalize_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.message = interaction.message
        session = await self.bot.db.get_vote_session(self.session_id)
        if session and session["status"] == "마감":
            return True
        await interaction.response.send_message(
            "이미 확정되었거나 종료된 참석 조사입니다.", ephemeral=True
        )
        return False

    async def _check_auto_confirm(self, channel) -> None:
        all_users = await self.bot.db.get_all_users(self.guild_id)
        response_count = await self.bot.db.get_attendance_response_count(
            self.session_id
        )
        if all_users and response_count >= len(all_users):
            await self._finalize(channel)

    async def _finalize(self, channel) -> bool:
        async with self._finalize_lock:
            attendees = await self.bot.db.get_attendees(self.session_id)
            if not attendees:
                await channel.send("⚠️ 참석자가 없어 확정할 수 없습니다.")
                return False

            if not await self.bot.db.confirm_vote_session(self.session_id):
                return False

            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

            session = await self.bot.db.get_vote_session(self.session_id)
            confirmed = _parse_datetime(session["confirmed_datetime"])
            mentions = " ".join(f"<@{att['user_id']}>" for att in attendees)
            embed = discord.Embed(
                title="🎉 일정 확정!",
                description=(
                    f"**{self.confirmed_slot}** 모임이 확정되었습니다.\n"
                    f"실제 일시: <t:{int(confirmed.timestamp())}:F>"
                ),
                color=discord.Color.green(),
            )
            embed.add_field(
                name=f"참석자 ({len(attendees)}명)", value=mentions, inline=False
            )
            embed.set_footer(text="이제 /게임선정 명령어로 플레이할 게임을 추첨하세요!")
            await channel.send(embed=embed)
            return True

    @discord.ui.button(
        label="참석",
        style=discord.ButtonStyle.success,
        custom_id="attendance:attend",
    )
    async def attend_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        attendee_count = await self.bot.db.set_attendance_response(
            self.session_id,
            interaction.user.id,
            True,
            self.confirmed_slot,
        )
        await interaction.response.send_message(
            f"✅ 참석으로 등록되었습니다. (현재 참석자: {attendee_count}명)",
            ephemeral=True,
        )
        await self._check_auto_confirm(interaction.channel)

    @discord.ui.button(
        label="불참",
        style=discord.ButtonStyle.danger,
        custom_id="attendance:absent",
    )
    async def absent_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.bot.db.set_attendance_response(
            self.session_id,
            interaction.user.id,
            False,
            self.confirmed_slot,
        )
        await interaction.response.send_message(
            "❌ 불참으로 등록되었습니다.", ephemeral=True
        )
        await self._check_auto_confirm(interaction.channel)

    @discord.ui.button(
        label="참석 확정 완료",
        style=discord.ButtonStyle.primary,
        custom_id="attendance:confirm",
    )
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "⚠️ 관리자만 참석을 확정할 수 있습니다.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self._finalize(interaction.channel)


class VoteView(discord.ui.View):
    def __init__(
        self,
        bot,
        guild_id: int,
        slots: list[dict[str, str]],
        deadline_at: datetime,
        session_id: int,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.slots = slots
        self.labels = [slot["label"] for slot in slots]
        self.session_id = session_id
        self.user_selections: dict[int, set[str]] = {}
        self.deadline_at = deadline_at
        self.closed = False
        self.message: discord.Message | None = None
        self._close_lock = asyncio.Lock()

        self.select = discord.ui.Select(
            placeholder="투표할 시간대를 선택하세요",
            min_values=0,
            max_values=len(self.labels),
            options=[
                discord.SelectOption(label=label, value=str(index))
                for index, label in enumerate(self.labels)
            ],
            custom_id="vote:select",
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.message = interaction.message
        session = await self.bot.db.get_vote_session(self.session_id)
        if session and session["status"] == "진행중":
            return True
        await interaction.response.send_message(
            "이미 마감되었거나 종료된 투표입니다.", ephemeral=True
        )
        return False

    def _ordered_selection(self, user_id: int) -> list[str]:
        selected = self.user_selections.get(user_id, set())
        return [label for label in self.labels if label in selected]

    async def _save_selection(self, user_id: int, *, completed: bool = False):
        await self.bot.db.upsert_vote_response(
            self.session_id,
            user_id,
            self._ordered_selection(user_id),
            is_completed=completed,
        )

    async def select_callback(self, interaction: discord.Interaction):
        selected_indices = [int(value) for value in self.select.values]
        selected = {self.labels[index] for index in selected_indices}
        self.user_selections[interaction.user.id] = selected
        await self._save_selection(interaction.user.id)
        await interaction.response.send_message(
            f"선택됨: {', '.join(self._ordered_selection(interaction.user.id)) or '없음'}",
            ephemeral=True,
        )
        await self.update_embed()

    @discord.ui.button(
        label="언제든지 (전체선택)",
        style=discord.ButtonStyle.secondary,
        custom_id="vote:all",
    )
    async def all_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.user_selections[interaction.user.id] = set(self.labels)
        await self._save_selection(interaction.user.id)
        await interaction.response.send_message(
            "모든 시간대를 선택했습니다.", ephemeral=True
        )
        await self.update_embed()

    @discord.ui.button(
        label="초기화",
        style=discord.ButtonStyle.secondary,
        custom_id="vote:clear",
    )
    async def clear_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.user_selections[interaction.user.id] = set()
        await self._save_selection(interaction.user.id)
        await interaction.response.send_message(
            "선택이 초기화되었습니다.", ephemeral=True
        )
        await self.update_embed()

    @discord.ui.button(
        label="투표 완료",
        style=discord.ButtonStyle.primary,
        custom_id="vote:done",
    )
    async def done_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._save_selection(interaction.user.id, completed=True)
        await interaction.response.send_message(
            "투표가 완료되었습니다.", ephemeral=True
        )
        await self.update_embed()

        completed_count = await self.bot.db.get_completed_vote_count(self.session_id)
        all_users = await self.bot.db.get_all_users(self.guild_id)
        if all_users and completed_count >= len(all_users):
            await self.close_vote(interaction.channel)

    @discord.ui.button(
        label="즉시 마감",
        style=discord.ButtonStyle.danger,
        custom_id="vote:close",
    )
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "관리자 권한이 필요합니다.", ephemeral=True
            )
            return
        await interaction.response.send_message("투표를 마감합니다.", ephemeral=True)
        await self.close_vote(interaction.channel)

    async def _tally(self) -> dict[str, int]:
        tally = {label: 0 for label in self.labels}
        for response in await self.bot.db.get_vote_responses(self.session_id):
            selected = _decode_selection(response.get("selected_slots"))
            self.user_selections[response["user_id"]] = selected
            for label in selected:
                if label in tally:
                    tally[label] += 1
        return tally

    async def update_embed(self):
        if not self.message or self.closed:
            return
        tally = await self._tally()
        description = (
            f"마감 시간: <t:{int(self.deadline_at.timestamp())}:R>\n\n"
            "**현재 투표 현황:**\n"
        )
        description += "\n".join(
            f"{label}: {tally[label]}표" for label in self.labels
        )
        embed = discord.Embed(
            title="🎮 일정 투표", description=description, color=discord.Color.blue()
        )
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass

    def _winning_slot(self, tally: dict[str, int]) -> str | None:
        max_votes = max(tally.values(), default=0)
        if max_votes == 0:
            return None
        candidates = [label for label, count in tally.items() if count == max_votes]

        def sort_key(label: str):
            weekend_priority = 0 if "(토)" in label or "(일)" in label else 1
            return weekend_priority, _parse_datetime(slot_datetime(self.slots, label))

        return min(candidates, key=sort_key)

    async def close_vote(self, channel, *, resume: bool = False):
        async with self._close_lock:
            if self.closed and not resume:
                return
            if not resume and not await self.bot.db.claim_vote_close(self.session_id):
                return
            self.closed = True

            vote_cog = self.bot.get_cog("VoteCog")
            if vote_cog:
                vote_cog.cancel_deadline(self.session_id)

            tally = await self._tally()
            winning_slot = self._winning_slot(tally)
            for item in self.children:
                item.disabled = True

            if winning_slot is None:
                await self.bot.db.cancel_vote_session(self.session_id)
                if self.message:
                    embed = discord.Embed(
                        title="투표 마감",
                        description="투표된 시간대가 없어 이번 투표가 취소되었습니다.",
                        color=discord.Color.red(),
                    )
                    try:
                        await self.message.edit(embed=embed, view=self)
                    except discord.HTTPException:
                        pass
                return

            confirmed_datetime = slot_datetime(self.slots, winning_slot)
            await self.bot.db.save_vote_winner(
                self.session_id, winning_slot, confirmed_datetime
            )

            result_lines = []
            for label in self.labels:
                marker = "🏆 " if label == winning_slot else ""
                result_lines.append(f"{marker}{label}: {tally[label]}표")
            if self.message:
                result_embed = discord.Embed(
                    title="일정 투표 결과",
                    description="\n".join(result_lines),
                    color=discord.Color.green(),
                )
                try:
                    await self.message.edit(embed=result_embed, view=self)
                except discord.HTTPException:
                    pass

            confirmed = _parse_datetime(confirmed_datetime)
            confirm_embed = discord.Embed(
                title="최종 일정 확정",
                description=(
                    f"가장 투표가 많은 시간대는 **{winning_slot}** 입니다.\n"
                    f"실제 일시: <t:{int(confirmed.timestamp())}:F>\n"
                    "참석 여부를 선택해주세요."
                ),
                color=discord.Color.blue(),
            )
            confirm_view = AttendanceView(
                self.bot, self.session_id, winning_slot, self.guild_id
            )
            attendance_message = await channel.send(
                embed=confirm_embed, view=confirm_view
            )
            confirm_view.message = attendance_message
            await self.bot.db.finish_vote_close(
                self.session_id, attendance_message.id
            )
            self.bot.add_view(confirm_view, message_id=attendance_message.id)
            if vote_cog:
                vote_cog.attendance_views[self.session_id] = confirm_view


class VoteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.deadline_tasks: dict[int, asyncio.Task] = {}
        self.vote_views: dict[int, VoteView] = {}
        self.attendance_views: dict[int, AttendanceView] = {}

    async def cog_load(self):
        await self.restore_sessions()

    def cog_unload(self):
        for task in self.deadline_tasks.values():
            task.cancel()

    async def shutdown(self) -> None:
        tasks = list(self.deadline_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.deadline_tasks.clear()

    async def _legacy_slots(self, session: dict) -> list[dict[str, str]]:
        config = await self.bot.db.get_or_create_config(session["guild_id"])
        start = _parse_datetime(session["start_date"] or session["created_at"])
        slots = build_vote_slots(
            start,
            config.get("day_time", "14:00"),
            config.get("night_time", "24:00"),
        )
        await self.bot.db.update_vote_session_slots(session["id"], slots)
        return slots

    async def _hydrate_vote(self, view: VoteView) -> None:
        for response in await self.bot.db.get_vote_responses(view.session_id):
            view.user_selections[response["user_id"]] = _decode_selection(
                response.get("selected_slots")
            )

    async def _resolve_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    async def _attach_message(self, view: VoteView, session: dict, channel) -> None:
        if session.get("message_id") and hasattr(channel, "fetch_message"):
            try:
                view.message = await channel.fetch_message(session["message_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                view.message = None

    async def _deadline_worker(
        self, view: VoteView, session: dict, *, resume: bool = False
    ) -> None:
        await self.bot.wait_until_ready()
        channel = await self._resolve_channel(session["channel_id"])
        await self._attach_message(view, session, channel)
        if not resume:
            remaining = (view.deadline_at - datetime.now(KST)).total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining)
        await view.close_vote(channel, resume=resume)

    def schedule_deadline(
        self, view: VoteView, session: dict, *, resume: bool = False
    ) -> None:
        self.cancel_deadline(view.session_id)
        task = asyncio.create_task(
            self._deadline_worker(view, session, resume=resume),
            name=f"vote-deadline-{view.session_id}",
        )
        self.deadline_tasks[view.session_id] = task

        def discard(completed: asyncio.Task) -> None:
            self.deadline_tasks.pop(view.session_id, None)
            if not completed.cancelled() and completed.exception():
                print(
                    f"❌ 투표 {view.session_id} 복구/마감 실패: "
                    f"{completed.exception()}"
                )

        task.add_done_callback(discard)

    def cancel_deadline(self, session_id: int) -> None:
        task = self.deadline_tasks.get(session_id)
        if task and task is not asyncio.current_task():
            task.cancel()

    async def restore_sessions(self) -> None:
        for session in await self.bot.db.get_recoverable_vote_sessions():
            slots = _decode_slots(session.get("slots"))
            if not slots:
                slots = await self._legacy_slots(session)

            if session["status"] in {"진행중", "마감처리"}:
                view = VoteView(
                    self.bot,
                    session["guild_id"],
                    slots,
                    _parse_datetime(session["deadline_at"]),
                    session["id"],
                )
                await self._hydrate_vote(view)
                self.vote_views[session["id"]] = view
                if session.get("message_id") and session["status"] == "진행중":
                    self.bot.add_view(view, message_id=session["message_id"])
                self.schedule_deadline(
                    view, session, resume=session["status"] == "마감처리"
                )
                continue

            if (
                session["status"] == "마감"
                and session.get("winning_slot")
                and session.get("attendance_message_id")
            ):
                view = AttendanceView(
                    self.bot,
                    session["id"],
                    session["winning_slot"],
                    session["guild_id"],
                )
                self.attendance_views[session["id"]] = view
                self.bot.add_view(
                    view, message_id=session["attendance_message_id"]
                )

    @app_commands.command(
        name="일정투표", description="다음 7일간의 게임 일정 투표를 생성합니다."
    )
    @app_commands.guild_only()
    async def create_vote(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        config = await self.bot.db.get_or_create_config(guild_id)
        now = datetime.now(KST)
        slots = build_vote_slots(
            now,
            config.get("day_time", "14:00"),
            config.get("night_time", "24:00"),
        )
        deadline_at = now + timedelta(
            hours=config.get("vote_deadline_hours", 24)
        )

        await interaction.response.defer()
        try:
            session = await self.bot.db.create_vote_session(
                guild_id=guild_id,
                channel_id=interaction.channel_id,
                start_date=now.isoformat(),
                deadline_at=deadline_at.isoformat(),
                slots=slots,
            )
        except ActiveSessionError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return

        view = VoteView(self.bot, guild_id, slots, deadline_at, session["id"])
        embed = discord.Embed(
            title=f"🎮 제{session['session_number']}회 일정 투표",
            description=(
                f"마감 시간: <t:{int(deadline_at.timestamp())}:R>\n\n"
                "투표할 실제 시간대를 선택하세요."
            ),
            color=discord.Color.blue(),
        )
        try:
            message = await interaction.followup.send(
                embed=embed, view=view, wait=True
            )
        except Exception:
            await self.bot.db.cancel_vote_session(session["id"])
            raise

        view.message = message
        await self.bot.db.update_vote_session_message(session["id"], message.id)
        session["message_id"] = message.id
        self.vote_views[session["id"]] = view
        self.schedule_deadline(view, session)


async def setup(bot):
    await bot.add_cog(VoteCog(bot))
