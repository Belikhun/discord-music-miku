import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import yt_dlp
import functools
from enum import Enum
import math
import logging
import os
import random
import aiohttp
import re
from typing import Union, Optional

# === THIẾT LẬP VÀ CLASS HELPER ===
log = logging.getLogger(__name__)
AnyContext = Union[commands.Context, discord.Interaction]
YTDL_SEARCH_OPTIONS = {'format':'bestaudio/best','noplaylist':True,'nocheckcertificate':True,'ignoreerrors':False,'logtostderr':False,'quiet':True,'no_warnings':True,'default_search':'ytsearch10','source_address':'0.0.0.0','extract_flat':'search'}
YTDL_DOWNLOAD_OPTIONS = {'format':'bestaudio[ext=m4a]/bestaudio/best','outtmpl':'cache/%(id)s.%(ext)s','restrictfilenames':True,'noplaylist':True,'nocheckcertificate':True,'ignoreerrors':False,'logtostderr':False,'quiet':True,'no_warnings':True,'source_address':'0.0.0.0','cachedir':False}
FFMPEG_OPTIONS = {'before_options':'','options':'-vn'}
class LoopMode(Enum): OFF = 0; SONG = 1; QUEUE = 2

class Song:
    """Đại diện cho một bài hát."""
    def __init__(self, data, requester: discord.Member | discord.User):
        self.requester = requester; self.data = data; self.url = data.get('webpage_url') or data.get('url')
        self.title = data.get('title'); self.thumbnail = data.get('thumbnail'); self.duration = data.get('duration')
        self.uploader = data.get('uploader'); self.filepath = None; self.id = data.get('id')
    def format_duration(self):
        if self.duration is None: return "N/A"
        m, s = divmod(self.duration, 60); h, m = divmod(m, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}" if h > 0 else f"{int(m):02d}:{int(s):02d}"
    def cleanup(self):
        if self.filepath and os.path.exists(self.filepath):
            try: os.remove(self.filepath); log.info(f"Đã xóa file cache: {self.filepath}")
            except OSError as e: log.error(f"Lỗi khi xóa file cache {self.filepath}: {e}")
    @classmethod
    async def search_only(cls, query: str, requester: discord.Member | discord.User):
        loop = asyncio.get_running_loop()
        partial = functools.partial(yt_dlp.YoutubeDL(YTDL_SEARCH_OPTIONS).extract_info, query, download=False)
        try:
            data = await loop.run_in_executor(None, partial);
            if not data or 'entries' not in data or not data['entries']: return []
            return [cls(entry, requester) for entry in data['entries']]
        except Exception as e: log.error(f"Lỗi yt-dlp khi TÌM KIẾM '{query}': {e}", exc_info=True); return []
    @classmethod
    async def from_url_and_download(cls, url: str, requester: discord.Member | discord.User):
        loop = asyncio.get_running_loop(); ytdl = yt_dlp.YoutubeDL(YTDL_DOWNLOAD_OPTIONS)
        partial = functools.partial(ytdl.extract_info, url, download=True)
        try:
            data = await loop.run_in_executor(None, partial);
            if not data: return None
            if 'entries' in data: data = data['entries'][0]
            song = cls(data, requester); song.filepath = ytdl.prepare_filename(data); return song
        except Exception as e: log.error(f"Lỗi yt-dlp khi TẢI VỀ '{url}': {e}", exc_info=True); return None

class SearchView(discord.ui.View):
    """Giao diện cho kết quả tìm kiếm."""
    def __init__(self, *, music_cog, ctx: AnyContext, results: list[Song]):
        super().__init__(timeout=180.0);self.music_cog=music_cog;self.ctx=ctx;self.requester=ctx.author if isinstance(ctx,commands.Context)else ctx.user;self.results=results;self.current_page=1;self.songs_per_page=5;self.total_pages=math.ceil(len(self.results)/self.songs_per_page);self.message=None;self.update_components()
    async def on_timeout(self):
        if self.message:
            try:await self.message.edit(content="Hết thời gian tìm kiếm.",embed=None,view=None)
            except discord.NotFound:pass
        self.stop()
    async def start(self):
        embed=self.create_page_embed()
        if isinstance(self.ctx, discord.Interaction):
            if self.ctx.response.is_done():self.message=await self.ctx.followup.send(embed=embed,view=self,ephemeral=True)
            else:await self.ctx.response.send_message(embed=embed,view=self,ephemeral=True);self.message=await self.ctx.original_response()
        else:self.message=await self.ctx.send(embed=embed,view=self)
    def update_components(self):self.prev_page_button.disabled=self.current_page==1;self.next_page_button.disabled=self.current_page>=self.total_pages;self.clear_items();self.add_item(self.create_select_menu());self.add_item(self.prev_page_button);self.add_item(self.next_page_button);self.add_item(self.cancel_button)
    def create_page_embed(self)->discord.Embed:start_index=(self.current_page-1)*self.songs_per_page;end_index=start_index+self.songs_per_page;page_results=self.results[start_index:end_index];description="".join(f"`{i+1}.` [{s.title}]({s.url})\n`{s.uploader or 'N/A'} - {s.format_duration()}`\n\n"for i,s in enumerate(page_results,start=start_index));embed=discord.Embed(title=f"🔎 Kết quả tìm kiếm (Trang {self.current_page}/{self.total_pages})",description=description,color=discord.Color.blue());embed.set_footer(text=f"Yêu cầu bởi {self.requester.display_name}",icon_url=self.requester.display_avatar.url);return embed
    def create_select_menu(self)->discord.ui.Select:start_index=(self.current_page-1)*self.songs_per_page;end_index=start_index+self.songs_per_page;options=[discord.SelectOption(label=f"{i+1}. {s.title[:80]}",value=str(i))for i,s in enumerate(self.results[start_index:end_index],start=start_index)];select=discord.ui.Select(placeholder="Chọn một bài hát để thêm...",options=options,custom_id="search_select_menu");select.callback=self.select_callback;return select
    async def select_callback(self,interaction:discord.Interaction):
        if interaction.user.id!=self.requester.id:return await interaction.response.send_message("Bạn không phải người yêu cầu!",ephemeral=True)
        await interaction.response.defer();await self.message.edit(content="⏳ Đang tải bài hát bạn chọn...",embed=None,view=None)
        selected_song=await Song.from_url_and_download(self.results[int(interaction.data["values"][0])].url,self.requester)
        if selected_song:
            state=self.music_cog.get_guild_state(interaction.guild_id)
            await state.queue.put(selected_song)
            if state.player_task is None or state.player_task.done():
                state.player_task=asyncio.create_task(state.player_loop())
            await self.message.edit(content=f"✅ Đã thêm **{selected_song.title}** vào hàng đợi.")
        else:
            await self.message.edit(content=f"❌ Rất tiếc, đã có lỗi khi tải về bài hát này.")
        self.stop()
    @discord.ui.button(label="Trước",style=discord.ButtonStyle.secondary,emoji="⬅️")
    async def prev_page_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id!=self.requester.id:return await interaction.response.send_message("Bạn không phải người yêu cầu!",ephemeral=True)
        self.current_page-=1;self.update_components();await interaction.response.edit_message(embed=self.create_page_embed(),view=self)
    @discord.ui.button(label="Sau",style=discord.ButtonStyle.secondary,emoji="➡️")
    async def next_page_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id!=self.requester.id:return await interaction.response.send_message("Bạn không phải người yêu cầu!",ephemeral=True)
        self.current_page+=1;self.update_components();await interaction.response.edit_message(embed=self.create_page_embed(),view=self)
    @discord.ui.button(label="Hủy",style=discord.ButtonStyle.danger,emoji="⏹️")
    async def cancel_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id!=self.requester.id:return await interaction.response.send_message("Bạn không phải người yêu cầu!",ephemeral=True)
        await self.message.edit(content="Đã hủy tìm kiếm.",embed=None,view=None);self.stop()

class GuildState:
    """Quản lý trạng thái của từng server."""
    def __init__(self, bot: commands.Bot, guild_id: int):self.bot=bot;self.guild_id=guild_id;self.queue=asyncio.Queue[Song]();self.voice_client:discord.VoiceClient|None=None;self.now_playing_message:discord.Message|None=None;self.current_song:Song|None=None;self.loop_mode=LoopMode.OFF;self.player_task:asyncio.Task|None=None;self.last_ctx:AnyContext|None=None;self.song_finished_event=asyncio.Event();self.volume=0.5
    async def player_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                previous_song=self.current_song
                if previous_song:
                    if self.loop_mode==LoopMode.SONG:log.info(f"Guild {self.guild_id}: Lặp lại bài hát '{previous_song.title}'.")
                    elif self.loop_mode==LoopMode.QUEUE:log.info(f"Guild {self.guild_id}: Thêm '{previous_song.title}' vào cuối hàng đợi lặp lại.");await self.queue.put(previous_song)
                    else:previous_song.cleanup()
                if self.loop_mode!=LoopMode.SONG or not self.current_song:self.current_song=await asyncio.wait_for(self.queue.get(),timeout=300)
                log.info(f"Guild {self.guild_id}: Lấy bài hát '{self.current_song.title}' từ hàng đợi.")
                await self.update_now_playing_message(new_song=True)
                source=discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(self.current_song.filepath,**FFMPEG_OPTIONS),volume=self.volume)
                self.song_finished_event.clear()
                self.voice_client.play(source,after=lambda e:self.bot.loop.call_soon_threadsafe(self.song_finished_event.set))
                await self.song_finished_event.wait()
                log.info(f"Guild {self.guild_id}: Sự kiện kết thúc bài hát '{self.current_song.title}' được kích hoạt.")
            except asyncio.TimeoutError:
                log.info(f"Guild {self.guild_id} không hoạt động trong 5 phút, bắt đầu dọn dẹp.")
                if self.last_ctx and self.last_ctx.channel:
                    try: await self.last_ctx.channel.send("😴 Đã tự động ngắt kết nối do không hoạt động.")
                    except discord.Forbidden: pass
                await self.cleanup();break
            except asyncio.CancelledError:log.info(f"Player loop cho guild {self.guild_id} đã bị hủy.");break
            except Exception as e:
                log.error(f"Lỗi nghiêm trọng trong player loop của guild {self.guild_id}:",exc_info=e)
                if self.last_ctx and self.last_ctx.channel:
                    try: await self.last_ctx.channel.send(f"🤖 Gặp lỗi nghiêm trọng, Miku cần khởi động lại trình phát nhạc. Lỗi: `{e}`")
                    except discord.Forbidden: pass
                await self.cleanup();break
    async def update_now_playing_message(self,new_song=False):
        if not self.last_ctx:return
        if not self.current_song and self.now_playing_message:
            try:await self.now_playing_message.delete()
            except discord.NotFound:pass
            self.now_playing_message=None;return
        if not self.current_song:return
        embed=self.create_now_playing_embed();view=self.create_control_view()
        if new_song and self.now_playing_message:
            try:await self.now_playing_message.delete()
            except discord.NotFound:pass
            self.now_playing_message=None
        if self.now_playing_message:
            try:await self.now_playing_message.edit(embed=embed,view=view);return
            except discord.NotFound:self.now_playing_message=None
        if not self.now_playing_message:
            try:self.now_playing_message=await self.last_ctx.channel.send(embed=embed,view=view)
            except(discord.Forbidden,discord.HTTPException)as e:log.warning(f"Không thể gửi/cập nhật tin nhắn Now Playing: {e}");self.now_playing_message=None
    def create_now_playing_embed(self)->discord.Embed:song=self.current_song;embed=discord.Embed(title=song.title,url=song.url,color=0x39d0d6);embed.set_author(name=f"Đang phát 🎵 (Âm lượng: {int(self.volume*100)}%)",icon_url=self.bot.user.display_avatar.url);embed.set_thumbnail(url=song.thumbnail);embed.add_field(name="Nghệ sĩ",value=song.uploader or 'N/A',inline=True);embed.add_field(name="Thời lượng",value=song.format_duration(),inline=True);embed.add_field(name="Yêu cầu bởi",value=song.requester.mention,inline=True);loop_status={LoopMode.OFF:"Tắt",LoopMode.SONG:"🔁 Bài hát",LoopMode.QUEUE:"🔁 Hàng đợi"};next_song_title="Không có" if self.queue.empty()else self.queue._queue[0].title[:50]+"...";total_songs=self.queue.qsize()+(1 if self.current_song else 0);embed.set_footer(text=f"Tiếp theo: {next_song_title} | Lặp: {loop_status[self.loop_mode]} | Tổng cộng: {total_songs} bài");return embed
    def create_control_view(self)->discord.ui.View:view=discord.ui.View(timeout=None);pause_resume_btn=discord.ui.Button(emoji="⏯️",style=discord.ButtonStyle.secondary,custom_id=f"ctrl_pause_{self.guild_id}");skip_btn=discord.ui.Button(emoji="⏭️",style=discord.ButtonStyle.secondary,custom_id=f"ctrl_skip_{self.guild_id}");stop_btn=discord.ui.Button(emoji="⏹️",style=discord.ButtonStyle.danger,custom_id=f"ctrl_stop_{self.guild_id}");loop_btn=discord.ui.Button(emoji="🔁",style=discord.ButtonStyle.secondary,custom_id=f"ctrl_loop_{self.guild_id}");queue_btn=discord.ui.Button(label="Hàng đợi",emoji="📜",style=discord.ButtonStyle.primary,custom_id=f"ctrl_queue_{self.guild_id}");pause_resume_btn.callback=self.pause_resume_callback;skip_btn.callback=self.skip_callback;stop_btn.callback=self.stop_callback;loop_btn.callback=self.loop_callback;queue_btn.callback=self.queue_callback;view.add_item(pause_resume_btn);view.add_item(skip_btn);view.add_item(stop_btn);view.add_item(loop_btn);view.add_item(queue_btn);return view
    async def pause_resume_callback(self,interaction:discord.Interaction):
        if self.voice_client.is_paused():self.voice_client.resume();await interaction.response.send_message("▶️ Đã tiếp tục phát.",ephemeral=True)
        else:self.voice_client.pause();await interaction.response.send_message("⏸️ Đã tạm dừng.",ephemeral=True)
    async def skip_callback(self,interaction:discord.Interaction):
        if self.voice_client and(self.voice_client.is_playing()or self.voice_client.is_paused()):self.voice_client.stop();await interaction.response.send_message("⏭️ Đã chuyển bài.",ephemeral=True)
        else:await interaction.response.send_message("Không có bài nào đang phát để chuyển.",ephemeral=True)
    async def stop_callback(self,interaction:discord.Interaction):await interaction.response.send_message("⏹️ Đang dừng phát nhạc và dọn dẹp hàng đợi...",ephemeral=True);await self.cleanup()
    async def loop_callback(self,interaction:discord.Interaction):self.loop_mode=LoopMode((self.loop_mode.value+1)%3);log.info(f"Guild {self.guild_id} đã đổi chế độ lặp thành {self.loop_mode.name}");mode_text={LoopMode.OFF:"Tắt lặp.",LoopMode.SONG:"🔁 Lặp lại bài hát hiện tại.",LoopMode.QUEUE:"🔁 Lặp lại toàn bộ hàng đợi."};await interaction.response.send_message(mode_text[self.loop_mode],ephemeral=True);await self.update_now_playing_message()
    async def queue_callback(self,interaction:discord.Interaction):
        embed = self._create_queue_embed()
        if not embed: return await interaction.response.send_message("Hàng đợi trống!", ephemeral=True)
        await interaction.response.send_message(embed=embed,ephemeral=True)
    def _create_queue_embed(self) -> discord.Embed | None:
        if self.queue.empty() and not self.current_song: return None
        embed = discord.Embed(title="📜 Hàng đợi bài hát", color=discord.Color.gold())
        if self.current_song: embed.add_field(name="▶️ Đang phát", value=f"[{self.current_song.title}]({self.current_song.url}) - Y/c bởi {self.current_song.requester.mention}", inline=False)
        queue_list = list(self.queue._queue)
        if queue_list:
            queue_text = "\n".join([f"`{i+1}.` [{song.title}]({song.url})" for i, song in enumerate(queue_list[:10])])
            if len(queue_list) > 10: queue_text += f"\n... và {len(queue_list) - 10} bài hát khác."
            embed.add_field(name="🎶 Tiếp theo", value=queue_text, inline=False)
        embed.set_footer(text=f"Tổng cộng: {len(queue_list) + (1 if self.current_song else 0)} bài hát"); return embed
    async def cleanup(self):
        log.info(f"Bắt đầu cleanup cho guild {self.guild_id}");self.bot.dispatch("session_end",self.guild_id)
        if self.player_task:self.player_task.cancel()
        if self.current_song:self.current_song.cleanup()
        while not self.queue.empty():
            try:song=self.queue.get_nowait();song.cleanup()
            except asyncio.QueueEmpty:break
        if self.voice_client:await self.voice_client.disconnect(force=True);log.info(f"Đã ngắt kết nối voice client khỏi guild {self.guild_id}")
        if self.now_playing_message:
            try:await self.now_playing_message.delete()
            except discord.NotFound:pass

# === COG GENERAL ===
class General(commands.Cog):
    """Chứa các lệnh chung và xử lý sự kiện."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Tự động đồng bộ lệnh khi bot tham gia server mới."""
        log.info(f"Đã tham gia server mới: {guild.name} ({guild.id}). Bắt đầu đồng bộ lệnh...")
        try:
            await self.bot.tree.sync(guild=guild)
            log.info(f"Đã đồng bộ lệnh thành công cho {guild.name}.")
        except Exception as e:
            log.error(f"Lỗi khi đồng bộ lệnh cho server mới {guild.name}:", exc_info=e)

    def _create_help_embed(self) -> discord.Embed:
        prefix = self.bot.command_prefix
        embed = discord.Embed(
            title="✨ Menu trợ giúp của Miku ✨",
            description="Miku sẵn sàng giúp bạn thưởng thức âm nhạc tuyệt vời nhất! (´• ω •`) ♡",
            color=0x39d0d6 # Miku's color
        )
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.display_avatar.url)
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1319215782089199616/1384577698315370587/6482863b5c8c3328433411f2-anime-hatsune-miku-plush-toy-series-snow.gif?ex=6852eff7&is=68519e77&hm=c89ddf3b2d3d2801118f537a45a6b67fcdd77cdb5c28d17ec6df791a040bac23&")

        # --- Lệnh Âm Nhạc được chia nhỏ ---
        
        embed.add_field(
            name="🎧 Lệnh Âm Nhạc (Cơ bản)",
            value=f"""
            `play <tên/url>`: Phát hoặc tìm kiếm bài hát.
            `pause`: Tạm dừng/tiếp tục phát.
            `skip`: Bỏ qua bài hát hiện tại.
            `stop`: Dừng nhạc và rời kênh.
            """,
            inline=False
        )
        
        embed.add_field(
            name="📜 Lệnh Hàng đợi",
            value=f"""
            `queue`: Xem hàng đợi hiện tại.
            `shuffle`: Xáo trộn thứ tự hàng đợi.
            `remove <số>`: Xóa bài hát khỏi hàng đợi.
            `clear`: Xóa sạch hàng đợi.
            """,
            inline=False
        )

        embed.add_field(
            name="⚙️ Lệnh Tiện ích",
            value=f"""
            `nowplaying`: Hiển thị lại bảng điều khiển.
            `volume <0-200>`: Chỉnh âm lượng.
            `seek <thời gian>`: Tua nhạc (vd: `1:23`).
            `lyrics`: Tìm lời bài hát đang phát.
            """,
            inline=False
        )

        # --- Lệnh Chung ---
        general_commands_text = f"""
        `help`: Hiển thị bảng trợ giúp này.
        `ping`: Kiểm tra độ trễ của Miku.
        """
        embed.add_field(name="✨ Lệnh Chung", value=general_commands_text, inline=False)
        
        # --- Footer ---
        embed.set_footer(
            text=f"Sử dụng lệnh với / (slash) hoặc {prefix} (prefix) • HatsuneMikuv2 | Project Galaxy by imnhyneko.dev",
            icon_url="https://avatars.githubusercontent.com/u/119964287?v=4"
        )
        return embed
    
    @commands.command(name="help", aliases=['h'])
    async def prefix_help(self, ctx: commands.Context):
        embed = self._create_help_embed()
        await ctx.send(embed=embed)
    @commands.command(name="ping")
    async def prefix_ping(self, ctx: commands.Context):
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"Pong! 🏓 Độ trễ của Miku là `{latency}ms`. Nhanh như một nốt nhạc! 🎶")
    
    @app_commands.command(name="help", description="Hiển thị menu trợ giúp của Miku.")
    async def slash_help(self, interaction: discord.Interaction):
        embed = self._create_help_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)
    @app_commands.command(name="ping", description="Kiểm tra độ trễ của Miku.")
    async def slash_ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! 🏓 Độ trễ của Miku là `{latency}ms`. Nhanh như một nốt nhạc! 🎶", ephemeral=True)

# === COG MUSIC ===
class Music(commands.Cog):
    """Chứa các lệnh liên quan đến âm nhạc."""
    music_group = app_commands.Group(name="music", description="Các lệnh liên quan đến phát nhạc.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot; self.states = {}; self.session = aiohttp.ClientSession()
    def cog_unload(self): self.bot.loop.create_task(self.session.close())
    def get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.states: self.states[guild_id] = GuildState(self.bot, guild_id)
        return self.states[guild_id]
    @commands.Cog.listener()
    async def on_session_end(self, guild_id: int):
        if guild_id in self.states: log.info(f"Xóa GuildState của guild {guild_id} khỏi bộ nhớ."); del self.states[guild_id]
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member.guild.voice_client or member.bot: return
        vc = member.guild.voice_client
        if len(vc.channel.members) == 1:
            log.info(f"Bot ở một mình trong kênh {vc.channel.name}, sẽ tự ngắt kết nối sau 60s.")
            await asyncio.sleep(60)
            if vc and len(vc.channel.members) == 1:
                log.info(f"Vẫn chỉ có một mình, đang ngắt kết nối...")
                state = self.get_guild_state(member.guild.id)
                if state.last_ctx:
                    try: await state.last_ctx.channel.send("👋 Tạm biệt! Miku sẽ rời đi vì không có ai nghe cùng.")
                    except discord.Forbidden: pass
                await state.cleanup()
    async def _send_response(self, ctx: AnyContext, *args, **kwargs):
        ephemeral = kwargs.get('ephemeral', False)
        if isinstance(ctx, discord.Interaction):
            if ctx.response.is_done(): await ctx.followup.send(*args, **kwargs)
            else: await ctx.response.send_message(*args, **kwargs)
        else: kwargs.pop('ephemeral', None); await ctx.send(*args, **kwargs)
    async def _play_logic(self, ctx: AnyContext, query: Optional[str]):
        state = self.get_guild_state(ctx.guild.id); state.last_ctx = ctx; author = ctx.author if isinstance(ctx, commands.Context) else ctx.user
        if not author.voice or not author.voice.channel: return await self._send_response(ctx, "Bạn phải ở trong một kênh thoại để dùng lệnh này!", ephemeral=True)
        if not query:
            if state.voice_client and state.voice_client.is_paused(): state.voice_client.resume(); await self._send_response(ctx, "▶️ Đã tiếp tục phát nhạc.", ephemeral=True)
            elif state.voice_client and state.voice_client.is_playing(): state.voice_client.pause(); await self._send_response(ctx, "⏯️ Đã tạm dừng nhạc.", ephemeral=True)
            else: await self._send_response(ctx, "Không có nhạc nào đang phát hoặc tạm dừng.", ephemeral=True)
            return
        if isinstance(ctx, discord.Interaction): await ctx.response.defer(ephemeral=False)
        else: await ctx.message.add_reaction("⏳")
        if not ctx.guild.voice_client: state.voice_client = await author.voice.channel.connect()
        else: await ctx.guild.voice_client.move_to(author.voice.channel); state.voice_client = ctx.guild.voice_client
        if query.startswith(('http://', 'https://')):
            song = await Song.from_url_and_download(query, author)
            if song:
                await state.queue.put(song); response_message = f"✅ Đã thêm **{song.title}** vào hàng đợi."
                if isinstance(ctx, discord.Interaction) and ctx.response.is_done(): await ctx.followup.send(response_message)
                else: await self._send_response(ctx, response_message)
                if state.player_task is None or state.player_task.done(): state.player_task = asyncio.create_task(state.player_loop())
            else: await self._send_response(ctx, f"❌ Không thể tải về từ URL: `{query}`")
        else:
            search_results = await Song.search_only(query, author)
            if not search_results: await self._send_response(ctx, f"❓ Không tìm thấy kết quả nào cho: `{query}`")
            else: search_view = SearchView(music_cog=self, ctx=ctx, results=search_results); await search_view.start()
        if isinstance(ctx, commands.Context): await ctx.message.remove_reaction("⏳", self.bot.user)
    async def _stop_logic(self, ctx: AnyContext):
        state = self.get_guild_state(ctx.guild.id)
        if state.voice_client: await self._send_response(ctx, "⏹️ Đã dừng phát nhạc và dọn dẹp hàng đợi."); await state.cleanup()
        else: await self._send_response(ctx, "Miku không ở trong kênh thoại nào cả.", ephemeral=True)
    async def _skip_logic(self, ctx: AnyContext):
        state = self.get_guild_state(ctx.guild.id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()): state.voice_client.stop(); await self._send_response(ctx, "⏭️ Đã chuyển bài.", ephemeral=True)
        else: await self._send_response(ctx, "Không có bài nào đang phát để chuyển.", ephemeral=True)
    async def _pause_logic(self, ctx: AnyContext):
        state = self.get_guild_state(ctx.guild.id)
        if state.voice_client and state.voice_client.is_playing(): state.voice_client.pause(); await self._send_response(ctx, "⏸️ Đã tạm dừng nhạc.", ephemeral=True)
        elif state.voice_client and state.voice_client.is_paused(): state.voice_client.resume(); await self._send_response(ctx, "▶️ Đã tiếp tục phát nhạc.", ephemeral=True)
        else: await self._send_response(ctx, "Không có nhạc nào đang phát để tạm dừng/tiếp tục.", ephemeral=True)
    async def _volume_logic(self, ctx: AnyContext, value: int):
        state = self.get_guild_state(ctx.guild.id)
        if not state.voice_client: return await self._send_response(ctx, "Miku chưa vào kênh thoại.", ephemeral=True)
        if not 0 <= value <= 200: return await self._send_response(ctx, "Âm lượng phải trong khoảng từ 0 đến 200.", ephemeral=True)
        state.volume = value / 100
        if state.voice_client.source: state.voice_client.source.volume = state.volume
        await self._send_response(ctx, f"🔊 Đã đặt âm lượng thành **{value}%**."); await state.update_now_playing_message()
    async def _seek_logic(self, ctx: AnyContext, timestamp: str):
        state = self.get_guild_state(ctx.guild.id);
        if not state.voice_client or not state.current_song:return await self._send_response(ctx,"Không có bài hát nào đang phát để tua.",ephemeral=True)
        match=re.match(r'(?:(\d+):)?(\d+)',timestamp)
        if not match:
            try:seconds=int(timestamp)
            except ValueError:return await self._send_response(ctx,"Định dạng thời gian không hợp lệ. Hãy dùng `phút:giây` hoặc `giây`.",ephemeral=True)
        else:minutes=int(match.group(1)or 0);seconds=int(match.group(2));seconds+=minutes*60
        if seconds>=state.current_song.duration:return await self._send_response(ctx,"Không thể tua vượt quá thời lượng bài hát.",ephemeral=True)
        ffmpeg_options_seek=FFMPEG_OPTIONS.copy();ffmpeg_options_seek['before_options']=f"-ss {seconds}";state.voice_client.stop();new_source=discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(state.current_song.filepath,**ffmpeg_options_seek),volume=state.volume);state.voice_client.play(new_source,after=lambda e:self.bot.loop.call_soon_threadsafe(state.song_finished_event.set));await self._send_response(ctx,f"⏩ Đã tua đến `{seconds}` giây.")
    async def _lyrics_logic(self,ctx:AnyContext):
        state=self.get_guild_state(ctx.guild.id)
        if not state.current_song:return await self._send_response(ctx,"Không có bài hát nào đang phát.",ephemeral=True)
        if isinstance(ctx,discord.Interaction):await ctx.response.defer(ephemeral=True)
        else:await ctx.message.add_reaction("🔍")
        title_search=re.sub(r'\(.*\)|\[.*\]|ft\..*','',state.current_song.title).strip();uploader_search=re.sub(r' - Topic','',state.current_song.uploader).strip()
        async with self.session.get(f"https://api.lyrics.ovh/v1/{uploader_search}/{title_search}")as resp:
            if resp.status!=200:msg=f"Rất tiếc, Miku không tìm thấy lời bài hát cho `{state.current_song.title}`. (´-ω-`)"
            else:
                data=await resp.json();lyrics=data.get('lyrics')
                if not lyrics:msg=f"Rất tiếc, Miku không tìm thấy lời bài hát cho `{state.current_song.title}`. (´-ω-`)"
                else:
                    embed=discord.Embed(title=f"🎤 Lời bài hát: {state.current_song.title}",color=0x39d0d6);embed.set_thumbnail(url=state.current_song.thumbnail)
                    if len(lyrics)>4096:lyrics=lyrics[:4090]+"\n..."
                    embed.description=lyrics;await self._send_response(ctx,embed=embed)
                    if isinstance(ctx,commands.Context):await ctx.message.remove_reaction("🔍",self.bot.user)
                    return
            await self._send_response(ctx,msg,ephemeral=True)
            if isinstance(ctx,commands.Context):await ctx.message.remove_reaction("🔍",self.bot.user)
    
    # --- PREFIX COMMANDS ---
    @commands.command(name="play", aliases=['p'])
    async def prefix_play(self, ctx: commands.Context, *, query: str = None): await self._play_logic(ctx, query)
    @commands.command(name="pause", aliases=['resume'])
    async def prefix_pause(self, ctx: commands.Context): await self._pause_logic(ctx)
    @commands.command(name="stop", aliases=['leave', 'disconnect'])
    async def prefix_stop(self, ctx: commands.Context): await self._stop_logic(ctx)
    @commands.command(name="skip", aliases=['s', 'fs'])
    async def prefix_skip(self, ctx: commands.Context): await self._skip_logic(ctx)
    @commands.command(name="queue", aliases=['q'])
    async def prefix_queue(self, ctx: commands.Context):
        state = self.get_guild_state(ctx.guild.id); embed = state._create_queue_embed()
        if not embed: await ctx.send("Hàng đợi trống!"); return
        await ctx.send(embed=embed)
    @commands.command(name="nowplaying", aliases=['np'])
    async def prefix_nowplaying(self, ctx: commands.Context):
        state = self.get_guild_state(ctx.guild.id); state.last_ctx = ctx; await state.update_now_playing_message(new_song=True)
    @commands.command(name="volume", aliases=['vol'])
    async def prefix_volume(self, ctx: commands.Context, value: int): await self._volume_logic(ctx, value)
    @commands.command(name="shuffle")
    async def prefix_shuffle(self, ctx: commands.Context):
        state = self.get_guild_state(ctx.guild.id)
        if state.queue.qsize()<2:return await ctx.send("Không đủ bài hát để xáo trộn.")
        queue_list=list(state.queue._queue);random.shuffle(queue_list)
        while not state.queue.empty():state.queue.get_nowait()
        for song in queue_list:await state.queue.put(song)
        await ctx.send("🔀 Đã xáo trộn hàng đợi!")
    @commands.command(name="remove")
    async def prefix_remove(self, ctx: commands.Context, index: int):
        state = self.get_guild_state(ctx.guild.id)
        if index <= 0 or index > state.queue.qsize():return await ctx.send("Số thứ tự không hợp lệ.")
        queue_list=list(state.queue._queue);removed_song=queue_list.pop(index-1);removed_song.cleanup()
        while not state.queue.empty():state.queue.get_nowait()
        for song in queue_list:await state.queue.put(song)
        await ctx.send(f"🗑️ Đã xóa **{removed_song.title}** khỏi hàng đợi.")
    @commands.command(name="clear")
    async def prefix_clear(self, ctx: commands.Context):
        state = self.get_guild_state(ctx.guild.id); count = 0
        while not state.queue.empty():
            try:song=state.queue.get_nowait();song.cleanup();count+=1
            except asyncio.QueueEmpty:break
        await ctx.send(f"💥 Đã xóa sạch {count} bài hát khỏi hàng đợi.")
    @commands.command(name="seek")
    async def prefix_seek(self, ctx: commands.Context, timestamp: str): await self._seek_logic(ctx, timestamp)
    @commands.command(name="lyrics", aliases=['ly'])
    async def prefix_lyrics(self, ctx: commands.Context): await self._lyrics_logic(ctx)
    
    # --- SLASH COMMANDS ---
    @music_group.command(name="play", description="Phát nhạc, thêm vào hàng đợi, hoặc tạm dừng/tiếp tục.")
    @app_commands.describe(query="Tên bài hát, URL, hoặc để trống để tạm dừng/tiếp tục.")
    async def slash_play(self, interaction: discord.Interaction, query: Optional[str] = None): await self._play_logic(interaction, query)
    @music_group.command(name="pause", description="Tạm dừng hoặc tiếp tục phát bài hát hiện tại.")
    async def slash_pause(self, interaction: discord.Interaction): await self._pause_logic(interaction)
    @music_group.command(name="stop", description="Dừng phát nhạc và ngắt kết nối.")
    async def slash_stop(self, interaction: discord.Interaction): await self._stop_logic(interaction)
    @music_group.command(name="skip", description="Bỏ qua bài hát hiện tại.")
    async def slash_skip(self, interaction: discord.Interaction): await self._skip_logic(interaction)
    @music_group.command(name="queue", description="Hiển thị hàng đợi bài hát.")
    async def slash_queue(self, interaction: discord.Interaction):
        state = self.get_guild_state(interaction.guild.id); state.last_ctx = interaction; await state.queue_callback(interaction)
    @music_group.command(name="nowplaying", description="Hiển thị lại bảng điều khiển nhạc.")
    async def slash_nowplaying(self, interaction: discord.Interaction):
        state = self.get_guild_state(interaction.guild.id); state.last_ctx = interaction; await state.update_now_playing_message(new_song=True); await interaction.response.send_message("Đã hiển thị lại bảng điều khiển.", ephemeral=True)
    @music_group.command(name="volume", description="Điều chỉnh âm lượng (0-200).")
    @app_commands.describe(value="Giá trị âm lượng từ 0 đến 200.")
    async def slash_volume(self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 200]): await self._volume_logic(interaction, value)
    @music_group.command(name="shuffle", description="Xáo trộn thứ tự các bài hát trong hàng đợi.")
    async def slash_shuffle(self, interaction: discord.Interaction):
        state = self.get_guild_state(interaction.guild.id)
        if state.queue.qsize()<2:return await interaction.response.send_message("Không đủ bài hát để xáo trộn.", ephemeral=True)
        queue_list=list(state.queue._queue);random.shuffle(queue_list)
        while not state.queue.empty():state.queue.get_nowait()
        for song in queue_list:await state.queue.put(song)
        await interaction.response.send_message("🔀 Đã xáo trộn hàng đợi!")
    @music_group.command(name="remove", description="Xóa một bài hát khỏi hàng đợi.")
    @app_commands.describe(index="Số thứ tự của bài hát trong hàng đợi (xem bằng /queue).")
    async def slash_remove(self, interaction: discord.Interaction, index: int):
        state = self.get_guild_state(interaction.guild.id)
        if index <= 0 or index > state.queue.qsize():return await interaction.response.send_message("Số thứ tự không hợp lệ.", ephemeral=True)
        queue_list=list(state.queue._queue);removed_song=queue_list.pop(index-1);removed_song.cleanup()
        while not state.queue.empty():state.queue.get_nowait()
        for song in queue_list:await state.queue.put(song)
        await interaction.response.send_message(f"🗑️ Đã xóa **{removed_song.title}** khỏi hàng đợi.")
    @music_group.command(name="clear", description="Xóa tất cả bài hát trong hàng đợi.")
    async def slash_clear(self, interaction: discord.Interaction):
        state = self.get_guild_state(interaction.guild.id); count = 0
        while not state.queue.empty():
            try:song=state.queue.get_nowait();song.cleanup();count+=1
            except asyncio.QueueEmpty:break
        await interaction.response.send_message(f"💥 Đã xóa sạch {count} bài hát khỏi hàng đợi.")
    @music_group.command(name="seek", description="Tua đến một thời điểm trong bài hát.")
    @app_commands.describe(timestamp="Thời gian để tua đến (vd: 1:23 hoặc 83).")
    async def slash_seek(self, interaction: discord.Interaction, timestamp: str): await self._seek_logic(interaction, timestamp)
    @music_group.command(name="lyrics", description="Tìm lời của bài hát đang phát.")
    async def slash_lyrics(self, interaction: discord.Interaction): await self._lyrics_logic(interaction)

async def setup(bot: commands.Bot):
    """Thiết lập và đăng ký các cogs vào bot."""
    await bot.add_cog(General(bot))
    await bot.add_cog(Music(bot))
    log.info("Đã thêm cogs General và Music.")
