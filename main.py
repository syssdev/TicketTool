import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import os
import json
from typing import Optional, Literal
import aiosqlite
from datetime import datetime as dt, timedelta

# MANUALLY LOAD .env FILE
def load_env():
    """Manually load .env file"""
    try:
        with open('.env', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
    except FileNotFoundError:
        print("ERROR: .env file not found!")
        print("Creating .env file template...")
        with open('.env', 'w') as f:
            f.write('''DISCORD_BOT_TOKEN=your_bot_token_here
GUILD_ID=your_server_id_here''')
        print("Please edit the .env file and add your bot token!")
        exit(1)

# Load environment variables
load_env()

# Bot Configuration
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GUILD_ID = int(os.environ.get('GUILD_ID', 0))

# DEBUG: Check if token is loaded
print(f"Token loaded: {TOKEN is not None}")
print(f"Token length: {len(TOKEN) if TOKEN else 0}")

if not TOKEN or TOKEN == "your_bot_token_here":
    print("ERROR: Invalid bot token! Please edit .env file")
    exit(1)

# Default values (can be changed via commands)
DEFAULT_CONFIG = {
    "ticket_category": 0,
    "archive_category": 0,
    "log_channel": 0,
    "transcript_channel": 0,
    "support_role": 0,
    "trainee_role": 0,
    "admin_role": 0,
    "panel_channel": 0,
    "panel_message": 0,
    "panel_title": "Support Tickets",
    "panel_description": "Click the button below to create a ticket",
    "panel_color": 0x9b59b6,
    "panel_image": None,
    "panel_thumbnail": None,
    "ticket_prefix": "ticket-",
    "max_tickets": 3,
    "auto_close_minutes": 30,  # 30 minutes for inactivity warning
    "auto_close_days": 1,     # 1 day for auto-close
    "require_reason": False,
    "work_start_hour": 10,    # 10 AM
    "work_end_hour": 22       # 10 PM
}

# Embed Colors
EMBED_COLORS = {
    "success": 0x2ecc71,
    "error": 0xe74c3c,
    "warning": 0xf39c12,
    "info": 0x3498db,
    "ticket": 0x9b59b6,
    "setup": 0x1abc9c,
    "staff": 0xe67e22,
    "off_hours": 0x95a5a6
}

# ============= DATABASE CLASS =============
class TicketDatabase:
    def __init__(self, db_path='tickets.db'):
        self.db_path = db_path
    
    async def init_db(self):
        """Initialize the database with migration"""
        async with aiosqlite.connect(self.db_path) as db:
            # Server configuration table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS server_config (
                    guild_id INTEGER PRIMARY KEY,
                    config TEXT,
                    updated_at TIMESTAMP
                )
            ''')
            
            # Tickets table - Create with all columns
            await db.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    creator_id INTEGER,
                    claimed_by INTEGER,
                    created_at TIMESTAMP,
                    closed_at TIMESTAMP,
                    status TEXT,
                    reason TEXT,
                    transcript TEXT,
                    last_user_response TIMESTAMP,
                    close_requested INTEGER DEFAULT 0,
                    inactivity_warning_sent INTEGER DEFAULT 0
                )
            ''')
            
            # Check columns exist
            cursor = await db.execute("PRAGMA table_info(tickets)")
            columns_data = await cursor.fetchall()
            column_names = [column[1] for column in columns_data]
            
            # Add missing columns
            columns_to_add = [
                ('last_user_response', 'TIMESTAMP'),
                ('close_requested', 'INTEGER DEFAULT 0'),
                ('inactivity_warning_sent', 'INTEGER DEFAULT 0')
            ]
            
            for column_name, column_type in columns_to_add:
                if column_name not in column_names:
                    print(f"Adding {column_name} column to tickets table...")
                    await db.execute(f'ALTER TABLE tickets ADD COLUMN {column_name} {column_type}')
            
            # Ticket members table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS ticket_members (
                    ticket_id INTEGER,
                    user_id INTEGER,
                    FOREIGN KEY (ticket_id) REFERENCES tickets (ticket_id)
                )
            ''')
            
            await db.commit()
            print("✅ Database initialized with migration")
    
    async def get_config(self, guild_id):
        """Get server configuration"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT config FROM server_config WHERE guild_id = ?',
                (guild_id,)
            )
            result = await cursor.fetchone()
            if result:
                return json.loads(result[0])
            return None
    
    async def update_config(self, guild_id, key, value):
        """Update server configuration"""
        config = await self.get_config(guild_id) or {}
        config[key] = value
        config_str = json.dumps(config)
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO server_config (guild_id, config, updated_at)
                VALUES (?, ?, ?)
            ''', (guild_id, config_str, dt.now().isoformat()))
            await db.commit()
        return config
    
    async def create_ticket(self, guild_id, channel_id, creator_id, reason):
        """Create a new ticket record"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO tickets (guild_id, channel_id, creator_id, created_at, status, reason, claimed_by, last_user_response, close_requested, inactivity_warning_sent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (guild_id, channel_id, creator_id, dt.now().isoformat(), 'open', reason, None, dt.now().isoformat(), 0, 0))
            await db.commit()
            return cursor.lastrowid
    
    async def update_last_response(self, channel_id):
        """Update last user response time"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET last_user_response = ?, inactivity_warning_sent = 0
                WHERE channel_id = ? AND status = 'open'
            ''', (dt.now().isoformat(), channel_id))
            await db.commit()
    
    async def mark_inactivity_warning_sent(self, channel_id):
        """Mark inactivity warning as sent"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET inactivity_warning_sent = 1
                WHERE channel_id = ? AND status = 'open'
            ''', (channel_id,))
            await db.commit()
    
    async def request_close(self, channel_id):
        """Request ticket closure"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET close_requested = 1
                WHERE channel_id = ? AND status = 'open'
            ''', (channel_id,))
            await db.commit()
    
    async def claim_ticket(self, channel_id, claimed_by):
        """Claim a ticket"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET claimed_by = ?
                WHERE channel_id = ? AND status = 'open'
            ''', (claimed_by, channel_id))
            await db.commit()
    
    async def unclaim_ticket(self, channel_id):
        """Unclaim a ticket"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET claimed_by = NULL
                WHERE channel_id = ? AND status = 'open'
            ''', (channel_id,))
            await db.commit()
    
    async def close_ticket(self, channel_id, closed_by):
        """Close a ticket"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET status = 'closed', closed_at = ?
                WHERE channel_id = ?
            ''', (dt.now().isoformat(), channel_id))
            await db.commit()
    
    async def get_ticket_by_channel(self, channel_id):
        """Get ticket by channel ID"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT * FROM tickets WHERE channel_id = ?',
                (channel_id,)
            )
            columns = [column[0] for column in cursor.description]
            result = await cursor.fetchone()
            if result:
                return dict(zip(columns, result))
            return None
    
    async def get_user_tickets(self, guild_id, user_id):
        """Get all tickets for a user"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''SELECT * FROM tickets 
                   WHERE guild_id = ? AND creator_id = ? AND status = 'open' ''',
                (guild_id, user_id)
            )
            columns = [column[0] for column in cursor.description]
            results = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in results]
    
    async def get_last_ticket_id(self):
        """Get the last ticket ID"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT MAX(ticket_id) FROM tickets'
            )
            result = await cursor.fetchone()
            return result[0] or 0
    
    async def get_inactive_tickets(self, guild_id, minutes=30):
        """Get tickets with no user response for X minutes"""
        cutoff_time = (dt.now() - timedelta(minutes=minutes)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''SELECT * FROM tickets 
                   WHERE guild_id = ? AND status = 'open' 
                   AND last_user_response < ? 
                   AND inactivity_warning_sent = 0 ''',
                (guild_id, cutoff_time)
            )
            columns = [column[0] for column in cursor.description]
            results = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in results]
    
    async def get_stale_tickets(self, guild_id, days=1):
        """Get tickets with no user response for X days"""
        cutoff_time = (dt.now() - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''SELECT * FROM tickets 
                   WHERE guild_id = ? AND status = 'open' 
                   AND last_user_response < ? ''',
                (guild_id, cutoff_time)
            )
            columns = [column[0] for column in cursor.description]
            results = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in results]

db = TicketDatabase()

# ============= BOT SETUP =============
# Ensure directories exist
os.makedirs("transcripts", exist_ok=True)
os.makedirs("panels", exist_ok=True)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

class TicketBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        self.guild_configs = {}
    
    async def get_config(self, guild_id, key=None):
        """Get configuration for a guild"""
        if guild_id not in self.guild_configs:
            config = await db.get_config(guild_id) or DEFAULT_CONFIG.copy()
            self.guild_configs[guild_id] = config
        
        if key:
            return self.guild_configs[guild_id].get(key, DEFAULT_CONFIG.get(key))
        return self.guild_configs[guild_id]

bot = TicketBot()

# ============= UI COMPONENTS =============
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket", emoji="🎫")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await bot.get_config(interaction.guild.id)
        
        # Check if reason is required
        if config.get("require_reason", False):
            modal = TicketReasonModal()
            await interaction.response.send_modal(modal)
        else:
            await interaction.response.defer(thinking=True, ephemeral=True)
            await create_ticket_channel(interaction, "No reason provided")

class TicketReasonModal(discord.ui.Modal, title="Create Ticket"):
    reason = discord.ui.TextInput(
        label="Reason for ticket",
        placeholder="Please describe your issue...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        await create_ticket_channel(interaction, self.reason.value)

# ============= TICKET FUNCTIONS =============
async def create_ticket_channel(interaction: discord.Interaction, reason: str):
    """Create a new ticket channel"""
    try:
        config = await bot.get_config(interaction.guild.id)
        
        # Check max tickets per user
        user_tickets = await db.get_user_tickets(interaction.guild.id, interaction.user.id)
        max_tickets = config.get("max_tickets", DEFAULT_CONFIG["max_tickets"])
        
        if len(user_tickets) >= max_tickets:
            embed = discord.Embed(
                title="❌ Ticket Limit Reached",
                description=f"You can only have {max_tickets} open tickets at a time.",
                color=EMBED_COLORS["error"]
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Get ticket category
        category_id = config.get("ticket_category")
        if not category_id:
            embed = discord.Embed(
                title="❌ Setup Required",
                description="Ticket category is not set up yet!\nUse `/setup category` to set it.",
                color=EMBED_COLORS["error"]
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        category = interaction.guild.get_channel(category_id)
        if not category:
            embed = discord.Embed(
                title="❌ Category Not Found",
                description="Ticket category not found! Please reconfigure with `/setup category`.",
                color=EMBED_COLORS["error"]
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Create channel
        last_ticket_id = await db.get_last_ticket_id()
        ticket_id = last_ticket_id + 1
        ticket_prefix = config.get("ticket_prefix", DEFAULT_CONFIG["ticket_prefix"])
        channel_name = f"{ticket_prefix}{ticket_id}"
        
        # Create overwrites - user can only read/send messages
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Add support role if exists
        support_role_id = config.get("support_role")
        support_role = None
        if support_role_id:
            support_role = interaction.guild.get_role(support_role_id)
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(
                    read_messages=True, 
                    send_messages=True,
                    manage_messages=True,
                    manage_channels=True
                )
        
        # Add trainee role if exists
        trainee_role_id = config.get("trainee_role")
        trainee_role = None
        if trainee_role_id:
            trainee_role = interaction.guild.get_role(trainee_role_id)
            if trainee_role:
                overwrites[trainee_role] = discord.PermissionOverwrite(
                    read_messages=True, 
                    send_messages=True,
                    manage_messages=True,
                    manage_channels=True
                )
        
        # Create channel
        ticket_channel = await category.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            reason=f"Ticket created by {interaction.user}"
        )
        
        # Create ticket in database
        await db.create_ticket(interaction.guild.id, ticket_channel.id, interaction.user.id, reason)
        
        # Get current time and check if staff is working
        current_hour = dt.now().hour
        work_start = config.get("work_start_hour", DEFAULT_CONFIG["work_start_hour"])
        work_end = config.get("work_end_hour", DEFAULT_CONFIG["work_end_hour"])
        
        # Create mentions string
        mentions = ""
        if support_role:
            mentions += f"{support_role.mention}, "
        if trainee_role:
            mentions += f"{trainee_role.mention}, "
        
        # Create the main ticket embed
        ticket_embed = discord.Embed(
            color=config.get("panel_color", EMBED_COLORS["ticket"]),
            timestamp=dt.now()
        )
        
        # Add ticket information
        ticket_embed.add_field(
            name=f"{interaction.user.display_name}",
            value=f"Hi there.\nThanks for opening a ticket. Please type your message here and a member of staff will be with you as soon as possible.",
            inline=False
        )
        
        ticket_embed.add_field(
            name="Important Notice",
            value="```Please do not ping our staff as they are volunteers and are very busy.```",
            inline=False
        )
        
        ticket_embed.add_field(
            name="What is this ticket regarding?",
            value=reason,
            inline=False
        )
        
        ticket_embed.set_footer(text=f"Ticket #{ticket_id} • Created at")
        
        # Send mentions and embed
        await ticket_channel.send(f"{mentions}{interaction.user.mention}")
        ticket_msg = await ticket_channel.send(embed=ticket_embed)
        
        # Send off-hours message if outside working hours
        if current_hour < work_start or current_hour >= work_end:
            hours_until_start = (work_start - current_hour) % 24
            off_hours_embed = discord.Embed(
                description=f"🕗 We're not working at the moment\nYou may receive a response before, but we don't start working until {work_start}:00 AM today (in {hours_until_start} hours).",
                color=EMBED_COLORS["off_hours"],
                timestamp=dt.now()
            )
            await ticket_channel.send(embed=off_hours_embed)
        
        # Pin the ticket message
        try:
            await ticket_msg.pin(reason="Ticket information")
        except:
            pass
        
        # Send confirmation to user
        confirm_embed = discord.Embed(
            title="✅ Ticket Created Successfully",
            description=f"Your ticket has been created: {ticket_channel.mention}",
            color=EMBED_COLORS["success"]
        )
        confirm_embed.add_field(name="Ticket ID", value=f"#{ticket_id}")
        confirm_embed.add_field(name="Reason", value=reason[:100])
        
        await interaction.followup.send(embed=confirm_embed, ephemeral=True)
        
        # Log ticket creation
        await log_ticket_action(interaction.guild.id, f"🎫 Ticket #{ticket_id} created by {interaction.user}")
        
    except Exception as e:
        print(f"Error creating ticket: {e}")
        error_embed = discord.Embed(
            title="❌ Error Creating Ticket",
            description=f"An error occurred: {str(e)}",
            color=EMBED_COLORS["error"]
        )
        try:
            await interaction.followup.send(embed=error_embed, ephemeral=True)
        except:
            pass

async def close_ticket_channel(interaction: discord.Interaction):
    """Close a ticket channel - staff only"""
    # Check if user has support or trainee role
    config = await bot.get_config(interaction.guild.id)
    support_role_id = config.get("support_role")
    trainee_role_id = config.get("trainee_role")
    
    has_permission = False
    if support_role_id:
        support_role = interaction.guild.get_role(support_role_id)
        if support_role and support_role in interaction.user.roles:
            has_permission = True
    
    if trainee_role_id and not has_permission:
        trainee_role = interaction.guild.get_role(trainee_role_id)
        if trainee_role and trainee_role in interaction.user.roles:
            has_permission = True
    
    # Also allow admins
    if not has_permission and not interaction.user.guild_permissions.administrator:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need staff permissions to close tickets.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    
    if not ticket:
        await interaction.response.send_message("This is not a valid ticket channel.", ephemeral=True)
        return
    
    # Close ticket in database
    await db.close_ticket(interaction.channel.id, interaction.user.id)
    
    # Get config
    config = await bot.get_config(interaction.guild.id)
    
    # Generate transcript before closing
    transcript_path = await generate_transcript_file(interaction.channel, ticket)
    
    # Move to archive category if set
    archive_category_id = config.get("archive_category")
    if archive_category_id:
        archive_category = interaction.guild.get_channel(archive_category_id)
        if archive_category:
            await interaction.channel.edit(
                category=archive_category,
                name=f"closed-{interaction.channel.name}",
                sync_permissions=True
            )
            
            # Remove send permissions for everyone
            await interaction.channel.set_permissions(
                interaction.guild.default_role,
                read_messages=False
            )
            
            # Send closing message
            embed = discord.Embed(
                title="🔒 Ticket Archived",
                description=f"This ticket has been closed by {interaction.user.mention}.\nThe channel has been moved to archives.",
                color=EMBED_COLORS["warning"],
                timestamp=dt.now()
            )
            embed.add_field(name="Ticket ID", value=f"#{ticket['ticket_id']}")
            embed.add_field(name="Duration", value=f"{(dt.now() - dt.fromisoformat(ticket['created_at'])).seconds // 60} minutes")
            await interaction.channel.send(embed=embed)
            
            # Send transcript to transcript channel
            await send_transcript_to_channel(interaction.guild.id, transcript_path, ticket, interaction.user)
            
            await interaction.response.send_message("Ticket archived and transcript saved.", ephemeral=True)
            return
    
    # If no archive category, close normally
    embed = discord.Embed(
        title="🔒 Ticket Closed",
        description=f"This ticket has been closed by {interaction.user.mention}.\nChannel will be deleted in 10 seconds.",
        color=EMBED_COLORS["warning"],
        timestamp=dt.now()
    )
    embed.add_field(name="Ticket ID", value=f"#{ticket['ticket_id']}")
    embed.add_field(name="Duration", value=f"{(dt.now() - dt.fromisoformat(ticket['created_at'])).seconds // 60} minutes")
    await interaction.channel.send(embed=embed)
    
    # Send transcript to transcript channel
    await send_transcript_to_channel(interaction.guild.id, transcript_path, ticket, interaction.user)
    
    await interaction.response.send_message("Ticket closed. Generating transcript...", ephemeral=True)
    
    # Wait and delete channel
    await asyncio.sleep(10)
    await interaction.channel.delete()

async def generate_transcript_file(channel: discord.TextChannel, ticket_info: dict) -> str:
    """Generate transcript file and return file path"""
    transcript = []
    
    # Header
    transcript.append("=" * 60)
    transcript.append(f"TICKET TRANSCRIPT - #{ticket_info['ticket_id']}")
    transcript.append("=" * 60)
    transcript.append(f"Created by: {ticket_info['creator_id']}")
    transcript.append(f"Created at: {ticket_info['created_at']}")
    if ticket_info.get('claimed_by'):
        transcript.append(f"Claimed by: {ticket_info['claimed_by']}")
    transcript.append(f"Status: {ticket_info['status']}")
    transcript.append(f"Reason: {ticket_info.get('reason', 'Not specified')}")
    transcript.append("=" * 60 + "\n")
    
    # Fetch messages
    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{message.author.name}#{message.author.discriminator}"
        content = message.clean_content
        
        # Handle attachments
        attachments = ""
        if message.attachments:
            attachments = " [Attachments: " + ", ".join(a.filename for a in message.attachments) + "]"
        
        # Handle embeds
        embeds = ""
        if message.embeds:
            embeds = " [Embeds: " + str(len(message.embeds)) + "]"
        
        transcript.append(f"[{timestamp}] {author}: {content}{attachments}{embeds}")
    
    # Footer
    transcript.append("\n" + "=" * 60)
    transcript.append(f"Transcript generated at: {dt.now().isoformat()}")
    transcript.append(f"Total messages: {len(transcript) - 10}")
    transcript.append("=" * 60)
    
    # Save to file
    transcript_text = "\n".join(transcript)
    filename = f"transcripts/ticket-{ticket_info['ticket_id']}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(transcript_text)
    
    return filename

async def send_transcript_to_channel(guild_id, transcript_path, ticket_info, closed_by):
    """Send transcript to configured transcript channel"""
    config = await bot.get_config(guild_id)
    transcript_channel_id = config.get("transcript_channel")
    
    if transcript_channel_id:
        guild = bot.get_guild(guild_id)
        if guild:
            transcript_channel = guild.get_channel(transcript_channel_id)
            if transcript_channel:
                embed = discord.Embed(
                    title=f"📜 Transcript - Ticket #{ticket_info['ticket_id']}",
                    color=EMBED_COLORS["info"],
                    timestamp=dt.now()
                )
                embed.add_field(name="Created by", value=f"<@{ticket_info['creator_id']}>", inline=True)
                embed.add_field(name="Reason", value=ticket_info.get('reason', 'Not specified')[:50], inline=True)
                embed.add_field(name="Status", value=ticket_info['status'].title(), inline=True)
                if ticket_info.get('claimed_by'):
                    embed.add_field(name="Claimed by", value=f"<@{ticket_info['claimed_by']}>", inline=True)
                embed.add_field(name="Created at", value=dt.fromisoformat(ticket_info['created_at']).strftime("%Y-%m-%d %H:%M:%S"), inline=True)
                embed.add_field(name="Closed at", value=dt.now().strftime("%Y-%m-%d %H:%M:%S"), inline=True)
                embed.add_field(name="Closed by", value=closed_by.mention, inline=True)
                
                # Create file
                file = discord.File(transcript_path, filename=f"transcript-{ticket_info['ticket_id']}.txt")
                await transcript_channel.send(embed=embed, file=file)

async def log_ticket_action(guild_id, message: str):
    """Log ticket actions to log channel"""
    config = await bot.get_config(guild_id)
    log_channel_id = config.get("log_channel")
    
    if log_channel_id:
        guild = bot.get_guild(guild_id)
        if guild:
            channel = guild.get_channel(log_channel_id)
            if channel:
                embed = discord.Embed(
                    description=message,
                    color=EMBED_COLORS["info"],
                    timestamp=dt.now()
                )
                await channel.send(embed=embed)

# ============= AUTO-CHECK TASKS =============
@tasks.loop(minutes=5)
async def check_inactive_tickets():
    """Check for tickets with no user response for 30 minutes"""
    for guild_id in bot.guild_configs:
        config = await bot.get_config(guild_id)
        auto_close_minutes = config.get("auto_close_minutes", DEFAULT_CONFIG["auto_close_minutes"])
        
        inactive_tickets = await db.get_inactive_tickets(guild_id, auto_close_minutes)
        
        for ticket in inactive_tickets:
            guild = bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(ticket['channel_id'])
                if channel:
                    # Send inactivity warning
                    warning_embed = discord.Embed(
                        title="⚠️ INACTIVITY WARNING",
                        description=f"No response in {auto_close_minutes} minutes will result in ticket closure.\nPlease respond to keep this ticket open.",
                        color=EMBED_COLORS["warning"],
                        timestamp=dt.now()
                    )
                    await channel.send(embed=warning_embed)
                    
                    # Mark warning as sent
                    await db.mark_inactivity_warning_sent(ticket['channel_id'])
                    
                    # Log warning
                    await log_ticket_action(guild_id, f"⚠️ Ticket #{ticket['ticket_id']} inactivity warning sent")

@tasks.loop(hours=1)
async def check_stale_tickets():
    """Check for tickets with no user response for 1 day"""
    for guild_id in bot.guild_configs:
        config = await bot.get_config(guild_id)
        auto_close_days = config.get("auto_close_days", DEFAULT_CONFIG["auto_close_days"])
        
        stale_tickets = await db.get_stale_tickets(guild_id, auto_close_days)
        
        for ticket in stale_tickets:
            guild = bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(ticket['channel_id'])
                if channel:
                    # Auto-close the ticket
                    await db.close_ticket(ticket['channel_id'], bot.user.id)
                    
                    # Send closure message
                    close_embed = discord.Embed(
                        title="🔒 TICKET AUTO-CLOSED",
                        description=f"This ticket has been automatically closed due to no response from the user for {auto_close_days} day(s).",
                        color=EMBED_COLORS["error"],
                        timestamp=dt.now()
                    )
                    await channel.send(embed=close_embed)
                    
                    # Generate and send transcript
                    try:
                        transcript_path = await generate_transcript_file(channel, ticket)
                        await send_transcript_to_channel(guild_id, transcript_path, ticket, bot.user)
                    except:
                        pass
                    
                    # Wait and delete/archive channel
                    await asyncio.sleep(10)
                    
                    # Move to archive category if set
                    archive_category_id = config.get("archive_category")
                    if archive_category_id:
                        archive_category = guild.get_channel(archive_category_id)
                        if archive_category:
                            await channel.edit(
                                category=archive_category,
                                name=f"closed-{channel.name}",
                                sync_permissions=True
                            )
                            await channel.set_permissions(
                                guild.default_role,
                                read_messages=False
                            )
                        else:
                            await channel.delete()
                    else:
                        await channel.delete()
                    
                    # Log auto-close
                    await log_ticket_action(guild_id, f"🚫 Ticket #{ticket['ticket_id']} auto-closed (no response for {auto_close_days} days)")

# ============= MESSAGE EVENT =============
@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Check if message is in a ticket channel
    ticket = await db.get_ticket_by_channel(message.channel.id)
    if ticket and ticket['status'] == 'open':
        # Update last user response time
        await db.update_last_response(message.channel.id)
    
    await bot.process_commands(message)

# ============= STAFF COMMANDS =============
@bot.tree.command(name="claim", description="Claim a ticket as staff")
async def claim(interaction: discord.Interaction):
    """Claim command for staff"""
    # Check if user has support or trainee role
    config = await bot.get_config(interaction.guild.id)
    support_role_id = config.get("support_role")
    trainee_role_id = config.get("trainee_role")
    
    has_permission = False
    if support_role_id:
        support_role = interaction.guild.get_role(support_role_id)
        if support_role and support_role in interaction.user.roles:
            has_permission = True
    
    if trainee_role_id and not has_permission:
        trainee_role = interaction.guild.get_role(trainee_role_id)
        if trainee_role and trainee_role in interaction.user.roles:
            has_permission = True
    
    if not has_permission:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need staff permissions to claim tickets.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    
    if not ticket:
        await interaction.response.send_message("This is not a valid ticket channel.", ephemeral=True)
        return
    
    if ticket.get('claimed_by'):
        claimed_by_user = await interaction.guild.fetch_member(ticket['claimed_by'])
        if claimed_by_user:
            embed = discord.Embed(
                title="❌ Ticket Already Claimed",
                description=f"This ticket is already claimed by {claimed_by_user.mention}",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    
    # Claim the ticket
    await db.claim_ticket(interaction.channel.id, interaction.user.id)
    
    # Send claim notification
    claim_embed = discord.Embed(
        title="✅ Ticket Claimed",
        description=f"{interaction.user.mention} has claimed this ticket.",
        color=EMBED_COLORS["success"],
        timestamp=dt.now()
    )
    claim_embed.set_footer(text=f"Claimed by {interaction.user}")
    await interaction.channel.send(embed=claim_embed)
    
    await interaction.response.send_message("You have claimed this ticket!", ephemeral=True)
    
    # Log ticket claim
    await log_ticket_action(interaction.guild.id, f"👤 Ticket #{ticket['ticket_id']} claimed by {interaction.user}")

@bot.tree.command(name="unclaim", description="Unclaim a ticket")
async def unclaim(interaction: discord.Interaction):
    """Unclaim command for staff"""
    # Check if user has support or trainee role
    config = await bot.get_config(interaction.guild.id)
    support_role_id = config.get("support_role")
    trainee_role_id = config.get("trainee_role")
    
    has_permission = False
    if support_role_id:
        support_role = interaction.guild.get_role(support_role_id)
        if support_role and support_role in interaction.user.roles:
            has_permission = True
    
    if trainee_role_id and not has_permission:
        trainee_role = interaction.guild.get_role(trainee_role_id)
        if trainee_role and trainee_role in interaction.user.roles:
            has_permission = True
    
    if not has_permission:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need staff permissions to unclaim tickets.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    
    if not ticket:
        await interaction.response.send_message("This is not a valid ticket channel.", ephemeral=True)
        return
    
    if not ticket.get('claimed_by'):
        embed = discord.Embed(
            title="❌ Ticket Not Claimed",
            description="This ticket is not currently claimed.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Check if user is the one who claimed it or has admin
    if ticket['claimed_by'] != interaction.user.id:
        # Check if user is admin
        if not interaction.user.guild_permissions.administrator:
            claimed_by_user = await interaction.guild.fetch_member(ticket['claimed_by'])
            if claimed_by_user:
                embed = discord.Embed(
                    title="❌ Not Your Ticket",
                    description=f"This ticket was claimed by {claimed_by_user.mention}. Only they or admins can unclaim it.",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
    
    # Unclaim the ticket
    await db.unclaim_ticket(interaction.channel.id)
    
    # Send unclaim notification
    unclaim_embed = discord.Embed(
        title="🔄 Ticket Unclaimed",
        description=f"{interaction.user.mention} has unclaimed this ticket.",
        color=EMBED_COLORS["warning"],
        timestamp=dt.now()
    )
    await interaction.channel.send(embed=unclaim_embed)
    
    await interaction.response.send_message("You have unclaimed this ticket!", ephemeral=True)
    
    # Log ticket unclaim
    await log_ticket_action(interaction.guild.id, f"👥 Ticket #{ticket['ticket_id']} unclaimed by {interaction.user}")

@bot.tree.command(name="close", description="Close the current ticket")
async def close(interaction: discord.Interaction):
    """Close command for staff"""
    await close_ticket_channel(interaction)

@bot.tree.command(name="add-user", description="Add a user to the current ticket")
@app_commands.describe(user="The user to add to the ticket")
async def add_user(interaction: discord.Interaction, user: discord.Member):
    """Add user to ticket command"""
    # Check if user has support or trainee role
    config = await bot.get_config(interaction.guild.id)
    support_role_id = config.get("support_role")
    trainee_role_id = config.get("trainee_role")
    
    has_permission = False
    if support_role_id:
        support_role = interaction.guild.get_role(support_role_id)
        if support_role and support_role in interaction.user.roles:
            has_permission = True
    
    if trainee_role_id and not has_permission:
        trainee_role = interaction.guild.get_role(trainee_role_id)
        if trainee_role and trainee_role in interaction.user.roles:
            has_permission = True
    
    if not has_permission:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need staff permissions to add users to tickets.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
    
    embed = discord.Embed(
        title="✅ User Added",
        description=f"{user.mention} has been added to the ticket by {interaction.user.mention}.",
        color=EMBED_COLORS["success"],
        timestamp=dt.now()
    )
    await interaction.response.send_message(embed=embed)
    
    # Also send in channel
    channel_embed = discord.Embed(
        title="👤 User Added",
        description=f"{user.mention} has been added to this ticket.",
        color=EMBED_COLORS["info"],
        timestamp=dt.now()
    )
    await interaction.channel.send(embed=channel_embed)

@bot.tree.command(name="remove-user", description="Remove a user from the current ticket")
@app_commands.describe(user="The user to remove from the ticket")
async def remove_user(interaction: discord.Interaction, user: discord.Member):
    """Remove user from ticket command"""
    # Check if user has support or trainee role
    config = await bot.get_config(interaction.guild.id)
    support_role_id = config.get("support_role")
    trainee_role_id = config.get("trainee_role")
    
    has_permission = False
    if support_role_id:
        support_role = interaction.guild.get_role(support_role_id)
        if support_role and support_role in interaction.user.roles:
            has_permission = True
    
    if trainee_role_id and not has_permission:
        trainee_role = interaction.guild.get_role(trainee_role_id)
        if trainee_role and trainee_role in interaction.user.roles:
            has_permission = True
    
    if not has_permission:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need staff permissions to remove users from tickets.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Don't remove the ticket creator
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    if ticket and user.id == ticket['creator_id']:
        embed = discord.Embed(
            title="❌ Cannot Remove",
            description="You cannot remove the ticket creator from their own ticket.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.channel.set_permissions(user, read_messages=False, send_messages=False)
    
    embed = discord.Embed(
        title="✅ User Removed",
        description=f"{user.mention} has been removed from the ticket by {interaction.user.mention}.",
        color=EMBED_COLORS["success"],
        timestamp=dt.now()
    )
    await interaction.response.send_message(embed=embed)
    
    # Also send in channel
    channel_embed = discord.Embed(
        title="👤 User Removed",
        description=f"{user.mention} has been removed from this ticket.",
        color=EMBED_COLORS["info"],
        timestamp=dt.now()
    )
    await interaction.channel.send(embed=channel_embed)

@bot.tree.command(name="transcript", description="Generate transcript for current ticket")
async def transcript(interaction: discord.Interaction):
    """Generate transcript command for staff"""
    # Check if user has support or trainee role
    config = await bot.get_config(interaction.guild.id)
    support_role_id = config.get("support_role")
    trainee_role_id = config.get("trainee_role")
    
    has_permission = False
    if support_role_id:
        support_role = interaction.guild.get_role(support_role_id)
        if support_role and support_role in interaction.user.roles:
            has_permission = True
    
    if trainee_role_id and not has_permission:
        trainee_role = interaction.guild.get_role(trainee_role_id)
        if trainee_role and trainee_role in interaction.user.roles:
            has_permission = True
    
    if not has_permission:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need staff permissions to generate transcripts.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    
    if not ticket:
        await interaction.response.send_message("This is not a valid ticket channel.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    # Create transcript file
    transcript_path = await generate_transcript_file(interaction.channel, ticket)
    
    # Send to user who requested
    file = discord.File(transcript_path, filename=f"transcript-{ticket['ticket_id']}.txt")
    await interaction.followup.send("Here's the transcript:", file=file, ephemeral=True)

@bot.tree.command(name="request-close", description="Request staff to close your ticket")
async def request_close(interaction: discord.Interaction):
    """User requests to close their ticket"""
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    
    if not ticket:
        await interaction.response.send_message("This is not a valid ticket channel.", ephemeral=True)
        return
    
    # Check if user is the ticket creator
    if ticket['creator_id'] != interaction.user.id:
        embed = discord.Embed(
            title="❌ Not Your Ticket",
            description="Only the ticket creator can request closure.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Check if close already requested
    if ticket.get('close_requested', 0) == 1:
        embed = discord.Embed(
            title="❌ Already Requested",
            description="Close request already sent. Staff will close the ticket soon.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Request closure
    await db.request_close(interaction.channel.id)
    
    # Send notification to staff
    staff_notice = discord.Embed(
        title="🚫 Close Requested",
        description=f"{interaction.user.mention} has requested to close this ticket.",
        color=EMBED_COLORS["warning"],
        timestamp=dt.now()
    )
    staff_notice.set_footer(text="Please close the ticket when ready")
    await interaction.channel.send(embed=staff_notice)
    
    # Send confirmation to user
    embed = discord.Embed(
        title="✅ Close Request Sent",
        description="Staff has been notified to close your ticket.",
        color=EMBED_COLORS["success"]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="force-close", description="Force close a ticket by ID (Admin only)")
@app_commands.describe(ticket_id="The ticket ID to force close")
async def force_close(interaction: discord.Interaction, ticket_id: int):
    """Force close a ticket - admin only"""
    if not interaction.user.guild_permissions.administrator:
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need administrator permissions to force close tickets.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Find ticket by ID
    async with aiosqlite.connect('tickets.db') as db_conn:
        cursor = await db_conn.execute(
            'SELECT * FROM tickets WHERE ticket_id = ? AND status = "open"',
            (ticket_id,)
        )
        ticket_data = await cursor.fetchone()
        
        if not ticket_data:
            embed = discord.Embed(
                title="❌ Ticket Not Found",
                description=f"No open ticket found with ID #{ticket_id}",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Get ticket as dict
        columns = [column[0] for column in cursor.description]
        ticket = dict(zip(columns, ticket_data))
        
        # Close ticket
        await db_conn.execute(
            'UPDATE tickets SET status = "closed", closed_at = ? WHERE ticket_id = ?',
            (dt.now().isoformat(), ticket_id)
        )
        await db_conn.commit()
    
    # Try to find and close the channel
    guild = interaction.guild
    channel = guild.get_channel(ticket['channel_id'])
    
    if channel:
        # Send closure message
        close_embed = discord.Embed(
            title="🔒 TICKET FORCE CLOSED",
            description=f"This ticket has been force closed by {interaction.user.mention}.",
            color=EMBED_COLORS["error"],
            timestamp=dt.now()
        )
        await channel.send(embed=close_embed)
        
        # Generate transcript
        try:
            transcript_path = await generate_transcript_file(channel, ticket)
            await send_transcript_to_channel(guild.id, transcript_path, ticket, interaction.user)
        except:
            pass
        
        # Wait and archive/delete
        await asyncio.sleep(10)
        
        config = await bot.get_config(guild.id)
        archive_category_id = config.get("archive_category")
        if archive_category_id:
            archive_category = guild.get_channel(archive_category_id)
            if archive_category:
                await channel.edit(
                    category=archive_category,
                    name=f"closed-{channel.name}",
                    sync_permissions=True
                )
                await channel.set_permissions(guild.default_role, read_messages=False)
            else:
                await channel.delete()
        else:
            await channel.delete()
    
    embed = discord.Embed(
        title="✅ Ticket Force Closed",
        description=f"Ticket #{ticket_id} has been force closed.",
        color=EMBED_COLORS["success"]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Log action
    await log_ticket_action(interaction.guild.id, f"🚫 Ticket #{ticket_id} force closed by {interaction.user}")

@bot.tree.command(name="ticket-info", description="Get information about current ticket")
async def ticket_info(interaction: discord.Interaction):
    """Get ticket information"""
    ticket = await db.get_ticket_by_channel(interaction.channel.id)
    
    if not ticket:
        await interaction.response.send_message("This is not a valid ticket channel.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"🎫 Ticket #{ticket['ticket_id']} Information",
        color=EMBED_COLORS["info"],
        timestamp=dt.now()
    )
    
    creator = await interaction.guild.fetch_member(ticket['creator_id'])
    embed.add_field(name="Created by", value=creator.mention if creator else "Unknown", inline=True)
    
    if ticket.get('claimed_by'):
        claimed_by = await interaction.guild.fetch_member(ticket['claimed_by'])
        embed.add_field(name="Claimed by", value=claimed_by.mention if claimed_by else "Unknown", inline=True)
    else:
        embed.add_field(name="Claimed by", value="Unclaimed", inline=True)
    
    created_at = dt.fromisoformat(ticket['created_at'])
    embed.add_field(name="Created at", value=created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    
    last_response = dt.fromisoformat(ticket['last_user_response'])
    time_since = dt.now() - last_response
    minutes_since = int(time_since.total_seconds() / 60)
    embed.add_field(name="Last user response", value=f"{minutes_since} minutes ago", inline=True)
    
    embed.add_field(name="Status", value=ticket['status'].title(), inline=True)
    embed.add_field(name="Reason", value=ticket.get('reason', 'Not specified')[:100], inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============= BOT EVENTS =============
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    
    # Initialize database
    await db.init_db()
    print('✅ Database initialized')
    
    # Sync commands
    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} commands')
    except Exception as e:
        print(f'❌ Error syncing commands: {e}')
    
    # Add persistent views
    bot.add_view(TicketView())
    print('✅ Persistent views added')
    
    # Start background tasks
    check_inactive_tickets.start()
    check_stale_tickets.start()
    print('✅ Background tasks started')

# ============= SETUP COMMAND =============
@bot.tree.command(name="setup", description="Setup the ticket system")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    """Main setup command with subcommands"""
    # This will show the setup options
    embed = discord.Embed(
        title="🎫 Ticket System Setup",
        description="Use the options below to configure your ticket system.",
        color=EMBED_COLORS["setup"]
    )
    
    embed.add_field(
        name="Available Commands",
        value="Use the dropdown menu below to select a setup option.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, view=SetupView(), ephemeral=True)

class SetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
    
    @discord.ui.select(
        placeholder="Select a setup option...",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Set Ticket Category", description="Where new tickets will be created", emoji="📁"),
            discord.SelectOption(label="Set Archive Category", description="Where closed tickets go", emoji="🗃️"),
            discord.SelectOption(label="Set Support Role", description="Role for support staff", emoji="🛡️"),
            discord.SelectOption(label="Set Trainee Role", description="Role for trainees", emoji="👨‍🎓"),
            discord.SelectOption(label="Set Log Channel", description="Channel for ticket logs", emoji="📋"),
            discord.SelectOption(label="Set Transcript Channel", description="Channel for transcripts", emoji="📜"),
            discord.SelectOption(label="Create Ticket Panel", description="Create the ticket creation panel", emoji="🎫"),
            discord.SelectOption(label="Customize Panel", description="Customize panel appearance", emoji="🎨"),
            discord.SelectOption(label="Set Ticket Prefix", description="Prefix for ticket channels", emoji="🏷️"),
            discord.SelectOption(label="Set Max Tickets", description="Max tickets per user", emoji="🔢"),
            discord.SelectOption(label="Set Inactivity Time", description="Minutes before inactivity warning", emoji="⏰"),
            discord.SelectOption(label="Set Auto-Close Days", description="Days before auto-close", emoji="🗑️"),
            discord.SelectOption(label="Set Working Hours", description="Staff working hours (24h)", emoji="🕐"),
            discord.SelectOption(label="Toggle Reason Requirement", description="Require reason for tickets", emoji="❓"),
            discord.SelectOption(label="View Settings", description="View current settings", emoji="⚙️"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        option = select.values[0]
        
        if option == "Set Ticket Category":
            await interaction.response.send_modal(CategoryModal("ticket_category", "Set Ticket Category", "Select the category where new tickets will be created:"))
        
        elif option == "Set Archive Category":
            await interaction.response.send_modal(CategoryModal("archive_category", "Set Archive Category", "Select the category where closed tickets will be moved:"))
        
        elif option == "Set Support Role":
            await interaction.response.send_modal(RoleModal("support_role", "Set Support Role", "Enter the Support role ID:"))
        
        elif option == "Set Trainee Role":
            await interaction.response.send_modal(RoleModal("trainee_role", "Set Trainee Role", "Enter the Trainee role ID:"))
        
        elif option == "Set Log Channel":
            await interaction.response.send_modal(ChannelModal("log_channel", "Set Log Channel", "Enter the Log channel ID:"))
        
        elif option == "Set Transcript Channel":
            await interaction.response.send_modal(ChannelModal("transcript_channel", "Set Transcript Channel", "Enter the Transcript channel ID:"))
        
        elif option == "Create Ticket Panel":
            await interaction.response.send_modal(PanelChannelModal())
        
        elif option == "Customize Panel":
            modal = PanelCustomizationModal()
            await interaction.response.send_modal(modal)
        
        elif option == "Set Ticket Prefix":
            await interaction.response.send_modal(TextModal("ticket_prefix", "Set Ticket Prefix", "Enter the prefix for ticket channels:", "ticket-"))
        
        elif option == "Set Max Tickets":
            await interaction.response.send_modal(NumberModal("max_tickets", "Set Max Tickets", "Enter maximum tickets per user (1-10):", 3, 1, 10))
        
        elif option == "Set Inactivity Time":
            await interaction.response.send_modal(NumberModal("auto_close_minutes", "Set Inactivity Time", "Enter minutes before inactivity warning (1-1440):", 30, 1, 1440))
        
        elif option == "Set Auto-Close Days":
            await interaction.response.send_modal(NumberModal("auto_close_days", "Set Auto-Close Days", "Enter days before auto-close (1-30):", 1, 1, 30))
        
        elif option == "Set Working Hours":
            await interaction.response.send_modal(WorkHoursModal())
        
        elif option == "Toggle Reason Requirement":
            await interaction.response.send_modal(BooleanModal("require_reason", "Toggle Reason Requirement", "Require reason when creating tickets? (true/false)"))
        
        elif option == "View Settings":
            await view_settings(interaction)

class CategoryModal(discord.ui.Modal, title="Set Category"):
    def __init__(self, config_key, title_text, description_text):
        super().__init__(title=title_text)
        self.config_key = config_key
        self.description_text = description_text
        
        self.category_id = discord.ui.TextInput(
            label="Category ID",
            placeholder="Enter the category ID...",
            required=True
        )
        self.add_item(self.category_id)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            category_id = int(self.category_id.value)
            category = interaction.guild.get_channel(category_id)
            
            if not category or not isinstance(category, discord.CategoryChannel):
                embed = discord.Embed(
                    title="❌ Invalid Category",
                    description="Please enter a valid category ID.",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            await db.update_config(interaction.guild.id, self.config_key, category_id)
            
            embed = discord.Embed(
                title="✅ Category Set",
                description=f"{self.description_text}\n**Set to:** {category.name}",
                color=EMBED_COLORS["success"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid ID",
                description="Please enter a valid numeric ID.",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class RoleModal(discord.ui.Modal, title="Set Role"):
    def __init__(self, config_key, title_text, description_text):
        super().__init__(title=title_text)
        self.config_key = config_key
        self.description_text = description_text
        
        self.role_id = discord.ui.TextInput(
            label="Role ID",
            placeholder="Enter the role ID...",
            required=True
        )
        self.add_item(self.role_id)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_id = int(self.role_id.value)
            role = interaction.guild.get_role(role_id)
            
            if not role:
                embed = discord.Embed(
                    title="❌ Invalid Role",
                    description="Please enter a valid role ID.",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            await db.update_config(interaction.guild.id, self.config_key, role_id)
            
            embed = discord.Embed(
                title="✅ Role Set",
                description=f"{self.description_text}\n**Set to:** {role.mention}",
                color=EMBED_COLORS["success"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid ID",
                description="Please enter a valid numeric ID.",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class ChannelModal(discord.ui.Modal, title="Set Channel"):
    def __init__(self, config_key, title_text, description_text):
        super().__init__(title=title_text)
        self.config_key = config_key
        self.description_text = description_text
        
        self.channel_id = discord.ui.TextInput(
            label="Channel ID",
            placeholder="Enter the channel ID...",
            required=True
        )
        self.add_item(self.channel_id)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)
            
            if not channel or not isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="❌ Invalid Channel",
                    description="Please enter a valid text channel ID.",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            await db.update_config(interaction.guild.id, self.config_key, channel_id)
            
            embed = discord.Embed(
                title="✅ Channel Set",
                description=f"{self.description_text}\n**Set to:** {channel.mention}",
                color=EMBED_COLORS["success"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid ID",
                description="Please enter a valid numeric ID.",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class PanelChannelModal(discord.ui.Modal, title="Create Ticket Panel"):
    def __init__(self):
        super().__init__()
        
        self.channel_id = discord.ui.TextInput(
            label="Channel ID",
            placeholder="Enter the channel ID for the panel...",
            required=True
        )
        self.add_item(self.channel_id)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)
            
            if not channel or not isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="❌ Invalid Channel",
                    description="Please enter a valid text channel ID.",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            config = await bot.get_config(interaction.guild.id)
            
            # Create panel embed
            embed = discord.Embed(
                title=config.get("panel_title", DEFAULT_CONFIG["panel_title"]),
                description=config.get("panel_description", DEFAULT_CONFIG["panel_description"]),
                color=config.get("panel_color", DEFAULT_CONFIG["panel_color"])
            )
            embed.set_footer(text="Click the button below to create a ticket")
            
            # Send panel
            view = TicketView()
            message = await channel.send(embed=embed, view=view)
            
            # Save panel info
            await db.update_config(interaction.guild.id, "panel_channel", channel.id)
            await db.update_config(interaction.guild.id, "panel_message", message.id)
            
            embed = discord.Embed(
                title="✅ Ticket Panel Created",
                description=f"Ticket panel has been created in {channel.mention}",
                color=EMBED_COLORS["success"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid ID",
                description="Please enter a valid numeric ID.",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class TextModal(discord.ui.Modal, title="Set Value"):
    def __init__(self, config_key, title_text, description_text, default_value=""):
        super().__init__(title=title_text)
        self.config_key = config_key
        self.description_text = description_text
        
        self.value_input = discord.ui.TextInput(
            label="Value",
            placeholder="Enter the value...",
            default=default_value,
            required=True
        )
        self.add_item(self.value_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        value = self.value_input.value
        
        await db.update_config(interaction.guild.id, self.config_key, value)
        
        embed = discord.Embed(
            title="✅ Value Set",
            description=f"{self.description_text}\n**Set to:** {value}",
            color=EMBED_COLORS["success"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class NumberModal(discord.ui.Modal, title="Set Number"):
    def __init__(self, config_key, title_text, description_text, default_value, min_value, max_value):
        super().__init__(title=title_text)
        self.config_key = config_key
        self.description_text = description_text
        self.min_value = min_value
        self.max_value = max_value
        
        self.value_input = discord.ui.TextInput(
            label="Value",
            placeholder=f"Enter a number between {min_value} and {max_value}...",
            default=str(default_value),
            required=True
        )
        self.add_item(self.value_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.value_input.value)
            
            if value < self.min_value or value > self.max_value:
                embed = discord.Embed(
                    title="❌ Invalid Value",
                    description=f"Value must be between {self.min_value} and {self.max_value}",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            await db.update_config(interaction.guild.id, self.config_key, value)
            
            embed = discord.Embed(
                title="✅ Value Set",
                description=f"{self.description_text}\n**Set to:** {value}",
                color=EMBED_COLORS["success"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Number",
                description="Please enter a valid number.",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class WorkHoursModal(discord.ui.Modal, title="Set Working Hours"):
    def __init__(self):
        super().__init__()
        
        self.start_hour = discord.ui.TextInput(
            label="Start Hour (0-23)",
            placeholder="Enter start hour (0-23)...",
            default="10",
            required=True
        )
        self.add_item(self.start_hour)
        
        self.end_hour = discord.ui.TextInput(
            label="End Hour (0-23)",
            placeholder="Enter end hour (0-23)...",
            default="22",
            required=True
        )
        self.add_item(self.end_hour)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            start = int(self.start_hour.value)
            end = int(self.end_hour.value)
            
            if start < 0 or start > 23 or end < 0 or end > 23:
                embed = discord.Embed(
                    title="❌ Invalid Hours",
                    description="Hours must be between 0 and 23",
                    color=EMBED_COLORS["error"]
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            await db.update_config(interaction.guild.id, "work_start_hour", start)
            await db.update_config(interaction.guild.id, "work_end_hour", end)
            
            embed = discord.Embed(
                title="✅ Working Hours Set",
                description=f"Staff working hours set to **{start}:00 - {end}:00**",
                color=EMBED_COLORS["success"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Number",
                description="Please enter valid numbers.",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class BooleanModal(discord.ui.Modal, title="Toggle Setting"):
    def __init__(self, config_key, title_text, description_text):
        super().__init__(title=title_text)
        self.config_key = config_key
        self.description_text = description_text
        
        self.value_input = discord.ui.TextInput(
            label="Value (true/false)",
            placeholder="Enter true or false...",
            required=True
        )
        self.add_item(self.value_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        value_str = self.value_input.value.lower()
        
        if value_str in ['true', 'yes', '1', 'enabled']:
            value = True
            status = "enabled"
        elif value_str in ['false', 'no', '0', 'disabled']:
            value = False
            status = "disabled"
        else:
            embed = discord.Embed(
                title="❌ Invalid Value",
                description="Please enter 'true' or 'false'",
                color=EMBED_COLORS["error"]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await db.update_config(interaction.guild.id, self.config_key, value)
        
        embed = discord.Embed(
            title="✅ Setting Updated",
            description=f"{self.description_text}\n**Now:** {status}",
            color=EMBED_COLORS["success"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def view_settings(interaction: discord.Interaction):
    """View current settings"""
    config = await bot.get_config(interaction.guild.id)
    
    embed = discord.Embed(
        title="⚙️ Ticket System Settings",
        color=EMBED_COLORS["setup"]
    )
    
    # Channel settings
    channels = []
    if config.get("ticket_category"):
        category = interaction.guild.get_channel(config["ticket_category"])
        channels.append(f"**Ticket Category:** {category.name if category else 'Not found'}")
    if config.get("archive_category"):
        category = interaction.guild.get_channel(config["archive_category"])
        channels.append(f"**Archive Category:** {category.name if category else 'Not found'}")
    if config.get("log_channel"):
        channel = interaction.guild.get_channel(config["log_channel"])
        channels.append(f"**Log Channel:** {channel.mention if channel else 'Not found'}")
    if config.get("transcript_channel"):
        channel = interaction.guild.get_channel(config["transcript_channel"])
        channels.append(f"**Transcript Channel:** {channel.mention if channel else 'Not found'}")
    if config.get("panel_channel"):
        channel = interaction.guild.get_channel(config["panel_channel"])
        channels.append(f"**Panel Channel:** {channel.mention if channel else 'Not found'}")
    
    if channels:
        embed.add_field(name="📁 Channels & Categories", value="\n".join(channels), inline=False)
    
    # Role settings
    roles = []
    if config.get("support_role"):
        role = interaction.guild.get_role(config["support_role"])
        roles.append(f"**Support Role:** {role.mention if role else 'Not found'}")
    if config.get("trainee_role"):
        role = interaction.guild.get_role(config["trainee_role"])
        roles.append(f"**Trainee Role:** {role.mention if role else 'Not found'}")
    
    if roles:
        embed.add_field(name="👥 Roles", value="\n".join(roles), inline=False)
    
    # Panel settings
    panel_info = []
    panel_info.append(f"**Panel Title:** {config.get('panel_title', DEFAULT_CONFIG['panel_title'])}")
    panel_info.append(f"**Panel Color:** #{hex(config.get('panel_color', DEFAULT_CONFIG['panel_color']))[2:]}")
    panel_info.append(f"**Ticket Prefix:** {config.get('ticket_prefix', DEFAULT_CONFIG['ticket_prefix'])}")
    panel_info.append(f"**Max Tickets/User:** {config.get('max_tickets', DEFAULT_CONFIG['max_tickets'])}")
    panel_info.append(f"**Inactivity Warning:** {config.get('auto_close_minutes', DEFAULT_CONFIG['auto_close_minutes'])} minutes")
    panel_info.append(f"**Auto-Close Days:** {config.get('auto_close_days', DEFAULT_CONFIG['auto_close_days'])}")
    panel_info.append(f"**Working Hours:** {config.get('work_start_hour', DEFAULT_CONFIG['work_start_hour'])}:00 - {config.get('work_end_hour', DEFAULT_CONFIG['work_end_hour'])}:00")
    panel_info.append(f"**Require Reason:** {config.get('require_reason', DEFAULT_CONFIG['require_reason'])}")
    
    embed.add_field(name="🎫 System Settings", value="\n".join(panel_info), inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============= ERROR HANDLING =============
@setup.error
async def setup_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need administrator permissions to use this command.",
            color=EMBED_COLORS["error"]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ============= RUN BOT =============
if __name__ == "__main__":
    print("Starting advanced ticket bot...")
    bot.run(TOKEN)
