import aiosqlite
import json
from datetime import datetime

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
                    transcript TEXT
                )
            ''')
            
            # Check if claimed_by column exists, if not add it
            cursor = await db.execute("PRAGMA table_info(tickets)")
            columns_data = await cursor.fetchall()
            column_names = [column[1] for column in columns_data]
            
            if 'claimed_by' not in column_names:
                print("Adding claimed_by column to tickets table...")
                await db.execute('ALTER TABLE tickets ADD COLUMN claimed_by INTEGER')
            
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
            ''', (guild_id, config_str, datetime.now().isoformat()))
            await db.commit()
        return config
    
    async def create_ticket(self, guild_id, channel_id, creator_id, reason):
        """Create a new ticket record"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO tickets (guild_id, channel_id, creator_id, created_at, status, reason, claimed_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (guild_id, channel_id, creator_id, datetime.now().isoformat(), 'open', reason, None))
            await db.commit()
            return cursor.lastrowid
    
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
            ''', (datetime.now().isoformat(), channel_id))
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

db = TicketDatabase()
