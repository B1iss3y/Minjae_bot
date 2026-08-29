import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import zoneinfo
import asyncio
import json

KST = zoneinfo.ZoneInfo("Asia/Seoul")
WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일']

class AttendanceView(discord.ui.View):
    def __init__(self, bot, session_id, confirmed_slot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.session_id = session_id
        self.confirmed_slot = confirmed_slot
        self.guild_id = guild_id
        self.responded_users: set[int] = set()  # 참석+불참 모두 추적
        self.confirmed = False

    async def _check_auto_confirm(self, channel):
        """등록 유저 전원이 응답했으면 자동 확정"""
        if self.confirmed:
            return
        all_users = await self.bot.db.get_all_users(self.guild_id)
        if len(all_users) > 0 and len(self.responded_users) >= len(all_users):
            await self._finalize(channel)

    async def _finalize(self, channel):
        """세션 확정 처리"""
        if self.confirmed:
            return
        self.confirmed = True

        attendees = await self.bot.db.get_attendees(self.session_id)
        if not attendees:
            await channel.send("⚠️ 참석자가 없어 확정할 수 없습니다.")
            self.confirmed = False
            return

        await self.bot.db.update_vote_session_status(self.session_id, "확정")

        # 버튼 비활성화
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self) if hasattr(self, 'message') else None
        except Exception:
            pass

        mentions = " ".join([f"<@{att['user_id']}>" for att in attendees])
        embed = discord.Embed(
            title="🎉 일정 확정!",
            description=f"**{self.confirmed_slot}** 모임이 확정되었습니다.",
            color=discord.Color.green(),
        )
        embed.add_field(name=f"참석자 ({len(attendees)}명)", value=mentions, inline=False)
        embed.set_footer(text="이제 /게임선정 명령어로 플레이할 게임을 추첨하세요!")
        await channel.send(embed=embed)

    @discord.ui.button(label="참석", style=discord.ButtonStyle.success)
    async def attend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.db.add_attendee(self.session_id, interaction.user.id, self.confirmed_slot)
        self.responded_users.add(interaction.user.id)
        attendees = await self.bot.db.get_attendees(self.session_id)
        await interaction.response.send_message(
            f"✅ 참석으로 등록되었습니다. (현재 참석자: {len(attendees)}명)", ephemeral=True
        )
        await self._check_auto_confirm(interaction.channel)

    @discord.ui.button(label="불참", style=discord.ButtonStyle.danger)
    async def absent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.db.remove_attendee(self.session_id, interaction.user.id)
        self.responded_users.add(interaction.user.id)
        await interaction.response.send_message("❌ 불참으로 등록되었습니다.", ephemeral=True)
        await self._check_auto_confirm(interaction.channel)

    @discord.ui.button(label="참석 확정 완료", style=discord.ButtonStyle.primary)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "⚠️ 관리자만 참석을 확정할 수 있습니다.", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self._finalize(interaction.channel)


class VoteView(discord.ui.View):
    def __init__(self, bot, guild_id, slots, deadline_at):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.slots = slots
        self.session_id = None
        self.user_selections = {}
        self.deadline_at = deadline_at
        self.closed = False
        self.message = None

        options = []
        for i, slot in enumerate(slots):
            options.append(discord.SelectOption(label=slot, value=str(i)))
        
        self.select = discord.ui.Select(
            placeholder='투표할 시간대를 선택하세요',
            min_values=0,
            max_values=14,
            options=options,
            custom_id="vote_select"
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def _save_selection(self, user_id: int):
        """현재 선택 상태를 DB에 자동 저장 (is_completed=False)"""
        if self.session_id is None:
            return
        slots_list = list(self.user_selections.get(user_id, set()))
        await self.bot.db.upsert_vote_response(
            self.session_id, user_id, slots_list, is_completed=False
        )

    async def select_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        selected_indices = [int(v) for v in self.select.values]
        selected_slots = [self.slots[i] for i in selected_indices]
        self.user_selections[user_id] = set(selected_slots)
        await interaction.response.send_message(f"선택됨: {', '.join(selected_slots) if selected_slots else '없음'}", ephemeral=True)
        await self._save_selection(user_id)
        await self.update_embed(interaction)

    @discord.ui.button(label="언제든지 (전체선택)", style=discord.ButtonStyle.secondary, custom_id="vote_all")
    async def all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        self.user_selections[user_id] = set(self.slots)
        await interaction.response.send_message("모든 시간대를 선택했습니다.", ephemeral=True)
        await self._save_selection(user_id)
        await self.update_embed(interaction)

    @discord.ui.button(label="초기화", style=discord.ButtonStyle.secondary, custom_id="vote_clear")
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        self.user_selections[user_id] = set()
        await interaction.response.send_message("선택이 초기화되었습니다.", ephemeral=True)
        await self._save_selection(user_id)
        await self.update_embed(interaction)

    @discord.ui.button(label="투표 완료", style=discord.ButtonStyle.primary, custom_id="vote_done")
    async def done_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        slots_list = list(self.user_selections.get(user_id, set()))
        await self.bot.db.upsert_vote_response(self.session_id, user_id, slots_list, is_completed=True)
        await interaction.response.send_message("투표가 완료되었습니다.", ephemeral=True)
        await self.update_embed(interaction)
        
        # Check if all users voted
        completed_count = await self.bot.db.get_completed_vote_count(self.session_id)
        all_users = await self.bot.db.get_all_users(self.guild_id)
        
        if completed_count >= len(all_users) and len(all_users) > 0:
            await self.close_vote(interaction.channel)

    @discord.ui.button(label="즉시 마감", style=discord.ButtonStyle.danger, custom_id="vote_close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
            return
        await interaction.response.send_message("투표를 마감합니다.", ephemeral=True)
        await self.close_vote(interaction.channel)

    async def update_embed(self, interaction=None):
        if not self.message or self.closed:
            return
        
        tally = {slot: 0 for slot in self.slots}
        for uid, slots in self.user_selections.items():
            for slot in slots:
                if slot in tally:
                    tally[slot] += 1
        
        desc = f"마감 시간: <t:{int(self.deadline_at.timestamp())}:R>\n\n**현재 투표 현황:**\n"
        for slot in self.slots:
            desc += f"{slot}: {tally[slot]}표\n"
        
        embed = self.message.embeds[0]
        embed.description = desc
        try:
            if interaction and not interaction.response.is_done():
                await interaction.message.edit(embed=embed)
            else:
                await self.message.edit(embed=embed)
        except Exception:
            pass

    async def close_vote(self, channel):
        if self.closed:
            return
        self.closed = True
        
        await self.bot.db.update_vote_session_status(self.session_id, '마감')
        
        for item in self.children:
            item.disabled = True
            
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

        responses = await self.bot.db.get_vote_responses(self.session_id)
        tally = {slot: 0 for slot in self.slots}
        for resp in responses:
            try:
                sel = json.loads(resp['selected_slots']) if isinstance(resp['selected_slots'], str) else resp['selected_slots']
                for s in sel:
                    if s in tally:
                        tally[s] += 1
            except Exception:
                pass

        max_votes = max(tally.values()) if tally else 0
        
        if max_votes == 0:
            embed = discord.Embed(title="투표 마감", description="투표된 시간대가 없습니다.", color=discord.Color.red())
            await channel.send(embed=embed)
            return

        candidates = [s for s, c in tally.items() if c == max_votes]
        
        def sort_key(slot):
            try:
                date_str, rest = slot.split('(')
                m, d = map(int, date_str.split('/'))
                yoil = rest[0]
                is_weekend = 0 if yoil in ['토', '일'] else 1
                return (is_weekend, m, d)
            except:
                return (1, 99, 99)
                
        candidates.sort(key=sort_key)
        winning_slot = candidates[0]

        result_desc = "**투표 결과**\n"
        for slot in self.slots:
            marker = "🏆 " if slot == winning_slot else ""
            result_desc += f"{marker}{slot}: {tally[slot]}표\n"

        result_embed = discord.Embed(title="일정 투표 결과", description=result_desc, color=discord.Color.green())
        await channel.send(embed=result_embed)
        
        confirm_embed = discord.Embed(
            title="최종 일정 확정",
            description=f"가장 투표가 많은 시간대는 **{winning_slot}** 입니다.\n참석 여부를 선택해주세요.",
            color=discord.Color.blue()
        )
        confirm_view = AttendanceView(self.bot, self.session_id, winning_slot, self.guild_id)
        await channel.send(embed=confirm_embed, view=confirm_view)


class VoteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="일정투표", description="다음 7일간의 게임 일정 투표를 생성합니다.")
    async def create_vote(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        config = await self.bot.db.get_or_create_config(guild_id)
        
        day_time = config.get('day_time', '14:00')
        night_time = config.get('night_time', '20:00')
        vote_deadline_hours = config.get('vote_deadline_hours', 24)

        now = datetime.now(KST)
        slots = []
        for i in range(1, 8):
            target_date = now + timedelta(days=i)
            md = target_date.strftime("%m/%d")
            yoil = WEEKDAYS[target_date.weekday()]
            slots.append(f"{md}({yoil}) 낮")
            slots.append(f"{md}({yoil}) 밤")

        deadline_at = now + timedelta(hours=vote_deadline_hours)
        
        view = VoteView(self.bot, guild_id, slots, deadline_at)
        
        embed = discord.Embed(
            title="🎮 일정 투표",
            description=f"마감 시간: <t:{int(deadline_at.timestamp())}:R>\n\n투표할 시간대를 선택하세요.",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        view.message = message
        
        session_number = await self.bot.db.increment_session_number(guild_id)
        
        session_id = await self.bot.db.create_vote_session(
            guild_id=guild_id,
            session_number=session_number,
            channel_id=interaction.channel_id,
            message_id=message.id,
            start_date=now.isoformat(),
            deadline_at=deadline_at.isoformat()
        )
        view.session_id = session_id
        
        async def auto_close():
            sleep_secs = (deadline_at - datetime.now(KST)).total_seconds()
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)
            if not view.closed:
                await view.close_vote(interaction.channel)
                
        asyncio.create_task(auto_close())

async def setup(bot):
    await bot.add_cog(VoteCog(bot))
