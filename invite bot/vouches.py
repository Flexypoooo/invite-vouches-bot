import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime

# Your bot's token and constants
TOKEN = "your_bot_token"
OWNER_ID =   # Replace with your Discord user ID
FOOTER_ICON_URL = "https://imgs.search.brave.com/L3X4ZKU-r8-qmyO99rjg0qUrcO58dcEBPanjpdEPNF0/rs:fit:860:0:0:0/g:ce/aHR0cHM6Ly9naWZk/Yi5jb20vaW1hZ2Vz/L2hpZ2gvYW5pbWUt/cGZwLWhvdXRhcm91/LW9yZWtpLWNvZmZl/ZS1obnN4NXpqZDMz/Y202ZzJ0LmdpZg.gif"  # Replace with your footer icon URL

# Setup intents
intents = discord.Intents.default()
intents.message_content = False  # We don't need content for now

bot = commands.Bot(command_prefix="!", intents=intents)

# Connect to DB and create table if not exists
conn = sqlite3.connect("vouches.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS vouches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    stars INTEGER NOT NULL,
    message TEXT NOT NULL,
    proof_url TEXT,
    vouched_by_id INTEGER NOT NULL,
    vouched_by_name TEXT NOT NULL,
    timestamp TEXT NOT NULL
)
""")
conn.commit()

# Helper function to check if attachment is an image
def is_valid_image(attachment: discord.Attachment) -> bool:
    if not attachment:
        return False
    return any(attachment.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg"])

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# /vouch command for everyone
@bot.tree.command(name="vouch", description="Leave a vouch for this server or user")
@app_commands.describe(stars="Rate from 1 to 5 stars", message="Your vouch message", proof="Optional image proof (png/jpg)")
@app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)  # Limit 1 vouch every 10 sec per user to avoid spam
async def vouch(interaction: discord.Interaction, stars: int, message: str, proof: discord.Attachment = None):
    # Validate stars
    if stars < 1 or stars > 5:
        await interaction.response.send_message("Stars must be between 1 and 5.", ephemeral=True)
        return

    # Validate proof image if provided
    proof_url = None
    if proof:
        if not is_valid_image(proof):
            await interaction.response.send_message("Proof must be a png, jpg, or jpeg image.", ephemeral=True)
            return
        # Download and re-upload image to Discord or just use the URL
        # For simplicity, use the Discord CDN URL
        proof_url = proof.url

    # Insert into DB
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    user = interaction.user
    try:
        c.execute(
            "INSERT INTO vouches (user_id, user_name, stars, message, proof_url, vouched_by_id, vouched_by_name, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user.id, str(user), stars, message, proof_url, user.id, str(user), timestamp)
        )
        conn.commit()
    except Exception as e:
        await interaction.response.send_message(f"Failed to save vouch: {e}", ephemeral=True)
        return

    # Create embed
    embed = discord.Embed(
        title="Thanks for vouching!",
        description=f"**{'⭐' * stars}**\n\n**Vouch:**\n{message}",
        color=discord.Color.purple(),
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Cheese Enterprises - Vouches!", icon_url=FOOTER_ICON_URL)
    if proof_url:
        embed.set_image(url=proof_url)

    await interaction.response.send_message(embed=embed)

# /restore_vouches command for owner only
@bot.tree.command(name="restore_vouches", description="Owner-only: List all saved vouches")
async def restore_vouches(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    c.execute("SELECT id, user_name, stars, message, proof_url, vouched_by_name, timestamp FROM vouches ORDER BY id DESC")
    rows = c.fetchall()

    if not rows:
        await interaction.response.send_message("No vouches found.", ephemeral=True)
        return

    # Send vouches in pages of 5 to avoid spam (optional)
    pages = []
    page = []
    for i, row in enumerate(rows, start=1):
        vouch_id, user_name, stars, message, proof_url, vouched_by_name, timestamp = row
        text = f"**Vouch #{vouch_id}** by {vouched_by_name} for {user_name}\nStars: {'⭐' * stars}\nMessage: {message}\nDate: {timestamp}"
        if proof_url:
            text += f"\nProof: {proof_url}"
        page.append(text)
        if i % 5 == 0:
            pages.append("\n\n".join(page))
            page = []
    if page:
        pages.append("\n\n".join(page))

    # Send first page with buttons to navigate if multiple pages
    # For simplicity, send only first 5 vouches in one message
    await interaction.response.send_message(f"**Vouches:**\n\n{pages[0]}")

bot.run(TOKEN)
