import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
TOKEN = os.getenv('DISCORD-BOT-TOKEN-HERE!')
GUILD_ID = int(os.getenv('SERVER-ID-HERE!', 0))

# Default values (can be changed via commands)
DEFAULT_CONFIG = {
    "ticket_category": 0,
    "archive_category": 0,
    "log_channel": 0,
    "transcript_channel": 0,
    "support_role": 0,
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
    "auto_close_days": 7,
    "require_reason": False
}

# Embed Colors
EMBED_COLORS = {
    "success": 0x2ecc71,
    "error": 0xe74c3c,
    "warning": 0xf39c12,
    "info": 0x3498db,
    "ticket": 0x9b59b6,
    "setup": 0x1abc9c
}
