import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from typing import List, Optional
from discord.ui import View, Button
from discord.ui import Select

# ---------------- CONFIG ----------------
TOKEN = "YOUR_BOT_TOKEN"
GUILD_ID =   # Your server ID for slash command sync
OWNER_ID =   # Your Discord user ID for admin commands
GUILD = discord.Object(id=GUILD_ID)

# ---------------- INTENTS ----------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
conn = sqlite3.connect("invites.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS registered_invites (
    inviter_id INTEGER PRIMARY KEY,
    invite_code TEXT NOT NULL UNIQUE
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS joins (
    member_id INTEGER PRIMARY KEY,
    inviter_id INTEGER NOT NULL,
    join_date TEXT NOT NULL
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""")

conn.commit()

# ---------------- HELPERS ----------------
guild_invites = {}

async def update_invites_cache(guild: discord.Guild):
    invites = await guild.invites()
    guild_invites[guild.id] = {invite.code: invite for invite in invites}

async def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    c.execute("SELECT value FROM settings WHERE key='log_channel_id'")
    row = c.fetchone()
    if row:
        try:
            return guild.get_channel(int(row[0]))
        except:
            return None
    return None

async def set_log_channel_db(channel_id: int):
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('log_channel_id', ?)", (str(channel_id),))
    conn.commit()

# ---------------- ADMIN CHECK ----------------
def is_owner(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID

def owner_only():
    def predicate(interaction: discord.Interaction):
        if not is_owner(interaction):
            raise app_commands.CheckFailure("You do not have permission to use this command.")
        return True
    return app_commands.check(predicate)

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    for guild in bot.guilds:
        await update_invites_cache(guild)
    await bot.tree.sync(guild=GUILD)
    print("Invite caches loaded. Slash commands synced for guild:", GUILD_ID)

@bot.event
async def on_guild_join(guild):
    await update_invites_cache(guild)

@bot.event
async def on_invite_create(invite):
    await update_invites_cache(invite.guild)

@bot.event
async def on_invite_delete(invite):
    await update_invites_cache(invite.guild)

@bot.event
async def on_member_join(member):
    guild = member.guild
    old_invites = guild_invites.get(guild.id)
    new_invites = await guild.invites()
    guild_invites[guild.id] = {invite.code: invite for invite in new_invites}

    used_invite = None
    if old_invites:
        for code, old_invite in old_invites.items():
            new_invite = guild_invites[guild.id].get(code)
            if new_invite and new_invite.uses > old_invite.uses:
                used_invite = new_invite
                break

    if not used_invite:
        return

    c.execute("SELECT inviter_id FROM registered_invites WHERE invite_code=?", (used_invite.code,))
    res = c.fetchone()
    if not res:
        return

    inviter_id = res[0]
    join_date = discord.utils.utcnow().isoformat()
    c.execute("INSERT OR IGNORE INTO joins (member_id, inviter_id, join_date) VALUES (?, ?, ?)",
              (member.id, inviter_id, join_date))
    conn.commit()

    log_channel = await get_log_channel(guild)
    if log_channel:
        embed = discord.Embed(
            title="New Member Joined",
            description=f"{member.mention} joined using invite `{used_invite.code}` from {used_invite.inviter.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Invite Tracker Bot", icon_url=bot.user.display_avatar.url)
        await log_channel.send(embed=embed)

from discord.ui import View, Button

# ---------------- REGISTER WITH OWNER APPROVAL ----------------
@bot.tree.command(name="register", description="Register your invite link (owner approval required)", guild=GUILD)
@app_commands.describe(invite_link="Your invite link")
async def register(interaction: discord.Interaction, invite_link: str):
    await interaction.response.defer(ephemeral=False)

    # Validate invite link
    if not ("discord.gg/" in invite_link or "discord.com/invite/" in invite_link):
        await interaction.followup.send("Invalid invite link format.", ephemeral=False)
        return

    try:
        invite = await bot.fetch_invite(invite_link)
    except:
        await interaction.followup.send("Invite link not found.", ephemeral=False)
        return

    if invite.guild.id != interaction.guild.id or invite.inviter is None:
        await interaction.followup.send("Invalid invite link for this server.", ephemeral=False)
        return

    owner_user = await bot.fetch_user(OWNER_ID)
    if not owner_user:
        await interaction.followup.send("Owner not found. Cannot request approval.", ephemeral=False)
        return

    # ---------------------------------------------------------------------
    # Approval Buttons
    # ---------------------------------------------------------------------
    class ApprovalView(View):
        def __init__(self, requester_id, invite_code):
            super().__init__(timeout=3600)
            self.requester_id = requester_id
            self.invite_code = invite_code

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
        async def approve(self, interaction: discord.Interaction, button: Button):

            # Owner only
            if interaction.user.id != OWNER_ID:
                await interaction.response.send_message("You are not allowed to approve this.", ephemeral=False)
                return

            guild = bot.get_guild(GUILD_ID)
            if guild is None:
                await interaction.response.send_message("Guild not found.", ephemeral=False)
                return

            # Update database
            c.execute("DELETE FROM registered_invites WHERE inviter_id=?", (self.requester_id,))
            c.execute(
                "INSERT INTO registered_invites (inviter_id, invite_code) VALUES (?, ?)",
                (self.requester_id, self.invite_code)
            )
            conn.commit()

            await update_invites_cache(guild)

            # Notify requester
            requester = await bot.fetch_user(self.requester_id)
            try:
                await requester.send(
                    f"✅ Your invite `{self.invite_code}` has been approved and is now tracked."
                )
            except:
                pass

            # Edit owner’s message
            await interaction.response.edit_message(
                content=f"✅ Invite `{self.invite_code}` approved and tracked.",
                view=None
            )

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
        async def deny(self, interaction: discord.Interaction, button: Button):

            if interaction.user.id != OWNER_ID:
                await interaction.response.send_message("You are not allowed to deny this.", ephemeral=False)
                return

            # Notify requester
            requester = await bot.fetch_user(self.requester_id)
            try:
                await requester.send(
                    f"❌ Your invite `{self.invite_code}` registration was denied by the owner."
                )
            except:
                pass

            # Edit owner's message
            await interaction.response.edit_message(
                content=f"❌ Invite `{self.invite_code}` registration denied.",
                view=None
            )

    # ---------------------------------------------------------------------
    # Send approval request to owner
    # ---------------------------------------------------------------------
    view = ApprovalView(interaction.user.id, invite.code)

    await owner_user.send(
        content=(
            f"<@{OWNER_ID}>\n"
            f"User **{interaction.user}** requested to register invite `{invite.code}`.\n"
            f"Approve or Deny:"
        ),
        view=view
    )

    # Confirm to user
    await interaction.followup.send(
        "✅ Your invite request has been sent to the owner for approval.",
        ephemeral=False
    )







# ---------------- LOG CHANNEL ----------------
@bot.tree.command(name="set_log_channel", description="Set log channel for join logs", guild=GUILD)
@owner_only()
@app_commands.describe(channel="Text channel to send join logs")
async def set_log_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    await set_log_channel_db(channel.id)
    await interaction.response.send_message(f"Log channel set to {channel.mention}", ephemeral=False)

# ---------------- INVITES PAGINATOR ----------------
class InvitesPaginator(View):
    def __init__(self, interaction: discord.Interaction, inviter_id: int, entries: List[int]):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.inviter_id = inviter_id
        self.entries = entries
        self.page = 0
        self.per_page = 10
        self.guild = interaction.guild

    def make_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_members = self.entries[start:end]

        members_in_guild = []
        left_members = []
        for mid in page_members:
            member = self.guild.get_member(mid)
            if member:
                members_in_guild.append(member.mention)
            else:
                left_members.append(f"<@{mid}>")

        embed = discord.Embed(
            title=f"{self.interaction.user.display_name}'s Invited Members (Page {self.page+1}/{max(1,(len(self.entries)+self.per_page-1)//self.per_page)})",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=self.interaction.user.display_avatar.url)
        if members_in_guild:
            embed.add_field(name=f"In server ({len(members_in_guild)})", value="\n".join(members_in_guild), inline=False)
        else:
            embed.add_field(name="In server", value="No members on this page.", inline=False)
        if left_members:
            embed.add_field(name=f"Left ({len(left_members)})", value="\n".join(left_members), inline=False)
        else:
            embed.add_field(name="Left", value="No members on this page.", inline=False)
        embed.set_footer(text=f"Total invited: {len(self.entries)} | Invite Tracker Bot", icon_url=bot.user.display_avatar.url)
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("Not your pagination.", ephemeral=False)
            return
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("Not your pagination.", ephemeral=False)
            return
        max_page = (len(self.entries)-1)//self.per_page
        if self.page < max_page:
            self.page += 1
            await interaction.response.edit_message(embed=self.make_embed(), view=self)

# ---------------- VIEW INVITES ----------------
@bot.tree.command(name="invites", description="View your invited members", guild=GUILD)
async def invites(interaction: discord.Interaction):
    user_id = interaction.user.id
    c.execute("SELECT member_id FROM joins WHERE inviter_id=?", (user_id,))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("No members joined with your invite.", ephemeral=False)
        return

    invited_ids = [r[0] for r in rows]
    view = InvitesPaginator(interaction, user_id, invited_ids)
    embed = view.make_embed()
    msg = await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    view.message = await msg.original_response()

# ---------------- LEADERBOARD ----------------
@bot.tree.command(name="leaderboard", description="Top inviters", guild=GUILD)
@owner_only()
async def leaderboard(interaction: discord.Interaction):
    c.execute("SELECT inviter_id, COUNT(*) FROM joins GROUP BY inviter_id ORDER BY COUNT(*) DESC LIMIT 10")
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("No invite data.", ephemeral=False)
        return
    embed = discord.Embed(title="Top Inviters", color=discord.Color.gold())
    guild = interaction.guild
    for i, (inviter_id, count) in enumerate(rows, 1):
        member = guild.get_member(inviter_id)
        name = member.display_name if member else f"<@{inviter_id}> (Left)"
        embed.add_field(name=f"{i}. {name}", value=f"Invited: {count}", inline=False)
    embed.set_footer(text="Invite Tracker Bot", icon_url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

# ---------------- ADMIN RESET ----------------
@bot.tree.command(name="reset_invites", description="Reset a user's invite data", guild=GUILD)
@owner_only()
@app_commands.describe(user="User to reset invites for")
async def reset_invites(interaction: discord.Interaction, user: discord.Member):
    c.execute("DELETE FROM registered_invites WHERE inviter_id=?", (user.id,))
    c.execute("DELETE FROM joins WHERE inviter_id=?", (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Invite data reset for {user.mention}", ephemeral=False)

@bot.tree.command(name="unregister", description="Unregister a user's invite link", guild=GUILD)
@owner_only()
@app_commands.describe(user="User to unregister invite for")
async def unregister(interaction: discord.Interaction, user: discord.Member):
    c.execute("DELETE FROM registered_invites WHERE inviter_id=?", (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Invite link unregistered for {user.mention}", ephemeral=False)

    # ---------------- NON-EXPIRING INVITE REQUEST ----------------
c.execute("""
CREATE TABLE IF NOT EXISTS invite_requests (
    requester_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL
)
""")
conn.commit()

class InviteApprovalView(View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=None)
        self.requester_id = requester_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        guild = bot.get_guild(GUILD_ID)
        member = guild.get_member(self.requester_id)
        if not member:
            await interaction.response.send_message("User not found.", ephemeral=False)
            return
        # Generate permanent invite
        channel = guild.text_channels[0]  # first text channel
        invite = await channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Approved non-expiring invite")
        # Update registered_invites
        c.execute("DELETE FROM registered_invites WHERE inviter_id=?", (self.requester_id,))
        c.execute("INSERT INTO registered_invites (inviter_id, invite_code) VALUES (?, ?)", (self.requester_id, invite.code))
        conn.commit()
        await update_invites_cache(guild)
        # DM user
        try:
            await member.send(f"✅ Your non-expiring invite has been approved: {invite.url}")
        except:
            pass
        await interaction.message.edit(content=f"✅ Approved non-expiring invite for <@{self.requester_id}>", view=None)
        c.execute("DELETE FROM invite_requests WHERE requester_id=?", (self.requester_id,))
        conn.commit()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: Button):
        member = bot.get_guild(GUILD_ID).get_member(self.requester_id)
        if member:
            try:
                await member.send("❌ Your request for a non-expiring invite was denied.")
            except:
                pass
        await interaction.message.edit(content=f"❌ Denied non-expiring invite for <@{self.requester_id}>", view=None)
        c.execute("DELETE FROM invite_requests WHERE requester_id=?", (self.requester_id,))
        conn.commit()

@bot.tree.command(name="request_invite", description="Request a non-expiring invite link", guild=GUILD)
async def request_invite(interaction: discord.Interaction):
    c.execute("SELECT status FROM invite_requests WHERE requester_id=?", (interaction.user.id,))
    if c.fetchone():
        await interaction.response.send_message("You already have a pending invite request.", ephemeral=False)
        return
    c.execute("INSERT INTO invite_requests (requester_id, status) VALUES (?, ?)", (interaction.user.id, "pending"))
    conn.commit()
    # DM owner with buttons
    owner = bot.get_user(OWNER_ID)
    view = InviteApprovalView(interaction.user.id)
    embed = discord.Embed(
        title="Non-expiring Invite Request",
        description=f"<@{interaction.user.id}> requested a non-expiring invite.\nApprove or Deny?",
        color=discord.Color.blue()
    )
    await owner.send(content=f"<@{OWNER_ID}>", embed=embed, view=view)  # ping owner in DM
    await interaction.response.send_message("✅ Your request has been sent to the owner for approval.", ephemeral=False)


#----------------------------------------------#invitelistowneronly----------------------------------------------


# -------------- OWNER CHECK --------------
def is_owner(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID

def owner_only():
    def predicate(interaction: discord.Interaction):
        if not is_owner(interaction):
            raise app_commands.CheckFailure("You are not allowed to use this command.")
        return True
    return app_commands.check(predicate)

# -------------- SELECT DROPDOWN FOR REMOVAL --------------
class InviteRemoveView(View):
    def __init__(self, invites):
        super().__init__(timeout=120)
        self.invites = invites  # list of tuples [(inviter_id, invite_code), ...]

        options = []
        for idx, (inviter_id, invite_code) in enumerate(invites, start=1):
            options.append(
                discord.SelectOption(
                    label=f"{idx}: {invite_code}",
                    description=f"User ID: {inviter_id}",
                    value=str(idx)  # dropdown value is index number
                )
            )

        self.select = Select(
            placeholder="Select an invite to remove...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.select.callback = self.remove_select
        self.add_item(self.select)

    async def remove_select(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("You are not the owner.", ephemeral=False)
            return

        chosen_index = int(self.select.values[0]) - 1
        inviter_id, invite_code = self.invites[chosen_index]

        # Remove from DB
        c.execute(
            "DELETE FROM registered_invites WHERE inviter_id=? AND invite_code=?",
            (inviter_id, invite_code)
        )
        conn.commit()

        await interaction.response.send_message(
            f"🗑️ Removed invite `{invite_code}` from <@{inviter_id}>.",
            ephemeral=False
        )

# -------------- OWNER COMMAND: INVITE LIST --------------
@bot.tree.command(name="invite_list", description="View and manage registered invites.", guild=GUILD)
@owner_only()
async def invite_list(interaction: discord.Interaction):

    # Fetch all registered invites
    c.execute("SELECT inviter_id, invite_code FROM registered_invites")
    rows = c.fetchall()

    if not rows:
        await interaction.response.send_message("No registered invites found.", ephemeral=False)
        return

    # Build embed
    embed = discord.Embed(
        title="📜 Registered Invite List",
        description="Below are all tracked invites with their associated users.",
        color=discord.Color.blurple()
    )

    for idx, (inviter_id, invite_code) in enumerate(rows, start=1):
        embed.add_field(
            name=f"{idx}. Invite Code: `{invite_code}`",
            value=f"👤 User: <@{inviter_id}> (`{inviter_id}`)",
            inline=False
        )

    # Make view with dropdown
    view = InviteRemoveView(rows)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

# ---------------- RUN ----------------
bot.run(TOKEN)
