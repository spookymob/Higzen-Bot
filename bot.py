import asyncio
import html
import logging
import os
import random
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import io
import json
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("discord-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL")
AUTO_ROLE_ID = os.getenv("AUTO_ROLE_ID")
TICKET_MOD_ROLE_ID = os.getenv("TICKET_MOD_ROLE_ID")
TICKET_CATEGORY_ID = os.getenv("TICKET_CATEGORY_ID")
TICKET_TRANSCRIPT_CHANNEL_ID = os.getenv("TICKET_TRANSCRIPT_CHANNEL_ID")
ORDER_PENDING_CHANNEL_ID = os.getenv("ORDER_PENDING_CHANNEL_ID")
ORDER_STORAGE_CHANNEL_ID = os.getenv("ORDER_STORAGE_CHANNEL_ID")
ORDER_COMPLETED_CHANNEL_ID = os.getenv("ORDER_COMPLETED_CHANNEL_ID")
ORDER_MOD_ROLE_ID = os.getenv("ORDER_MOD_ROLE_ID") or TICKET_MOD_ROLE_ID
SERVER_LOG_CHANNEL_ID = os.getenv("SERVER_LOG_CHANNEL_ID")
ANNOUNCEMENT_CHANNEL_ID = os.getenv("ANNOUNCEMENT_CHANNEL_ID")
DATABASE_PATH = "scheduled_messages.db"
IST = ZoneInfo("Asia/Kolkata")


class DiscordBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", GUILD_ID)
        else:
            await self.tree.sync()
            logger.info("Synced global commands")
        initialize_schedule_database()
        for giveaway_id, content in get_active_giveaway_views():
            self.add_view(GiveawayView(giveaway_id, content))
        for guild_id in get_ticket_panel_guild_ids():
            self.add_view(TicketPanelView(guild_id))
        for channel_id in get_open_ticket_ids():
            self.add_view(TicketView(channel_id))
        for order_id in get_open_order_ids():
            self.add_view(OrderView(order_id))
        self.process_scheduled_messages.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    @tasks.loop(seconds=20)
    async def process_scheduled_messages(self) -> None:
        due_messages = await asyncio.to_thread(get_due_messages)
        for message_id, channel_id, message_type, content, heading in due_messages:
            try:
                channel = await self.fetch_channel(channel_id)
                if isinstance(channel, discord.TextChannel):
                    await send_container_message(channel, message_type, content, heading)
                    await asyncio.to_thread(delete_scheduled_message, message_id)
            except (discord.DiscordException, ValueError):
                logger.exception("Could not send scheduled message %s", message_id)
        await refresh_active_giveaway_messages()
        await process_due_giveaways()

    @process_scheduled_messages.before_loop
    async def wait_for_ready(self) -> None:
        await self.wait_until_ready()


bot = DiscordBot()

ENV_SETTING_KEYS = [
    "WELCOME_CHANNEL_ID",
    "WELCOME_IMAGE_URL",
    "AUTO_ROLE_ID",
    "TICKET_MOD_ROLE_ID",
    "TICKET_CATEGORY_ID",
    "TICKET_TRANSCRIPT_CHANNEL_ID",
    "ORDER_PENDING_CHANNEL_ID",
    "ORDER_STORAGE_CHANNEL_ID",
    "ORDER_COMPLETED_CHANNEL_ID",
    "ORDER_MOD_ROLE_ID",
    "SERVER_LOG_CHANNEL_ID",
    "ANNOUNCEMENT_CHANNEL_ID",
]


def normalize_setting_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "" or normalized.lower() in {"none", "null", "clear"}:
        return None
    return normalized


def refresh_runtime_settings() -> None:
    global WELCOME_CHANNEL_ID
    global WELCOME_IMAGE_URL
    global AUTO_ROLE_ID
    global TICKET_MOD_ROLE_ID
    global TICKET_CATEGORY_ID
    global TICKET_TRANSCRIPT_CHANNEL_ID
    global ORDER_PENDING_CHANNEL_ID
    global ORDER_STORAGE_CHANNEL_ID
    global ORDER_COMPLETED_CHANNEL_ID
    global ORDER_MOD_ROLE_ID
    global SERVER_LOG_CHANNEL_ID
    global ANNOUNCEMENT_CHANNEL_ID

    WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID")
    WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL")
    AUTO_ROLE_ID = os.getenv("AUTO_ROLE_ID")
    TICKET_MOD_ROLE_ID = os.getenv("TICKET_MOD_ROLE_ID")
    TICKET_CATEGORY_ID = os.getenv("TICKET_CATEGORY_ID")
    TICKET_TRANSCRIPT_CHANNEL_ID = os.getenv("TICKET_TRANSCRIPT_CHANNEL_ID")
    ORDER_PENDING_CHANNEL_ID = os.getenv("ORDER_PENDING_CHANNEL_ID")
    ORDER_STORAGE_CHANNEL_ID = os.getenv("ORDER_STORAGE_CHANNEL_ID")
    ORDER_COMPLETED_CHANNEL_ID = os.getenv("ORDER_COMPLETED_CHANNEL_ID")
    ORDER_MOD_ROLE_ID = os.getenv("ORDER_MOD_ROLE_ID") or TICKET_MOD_ROLE_ID
    SERVER_LOG_CHANNEL_ID = os.getenv("SERVER_LOG_CHANNEL_ID")
    ANNOUNCEMENT_CHANNEL_ID = os.getenv("ANNOUNCEMENT_CHANNEL_ID")


def update_env_setting(key: str, value: str | None) -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()

    updated = False
    next_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            if value is not None:
                next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)

    if not updated and value is not None:
        next_lines.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as file:
        file.write("\n".join(next_lines))
        if next_lines:
            file.write("\n")

    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value

    load_dotenv(override=True)
    refresh_runtime_settings()


def initialize_schedule_database() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                heading TEXT NOT NULL DEFAULT '',
                scheduled_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                description TEXT NOT NULL,
                winner_count INTEGER NOT NULL,
                host_id INTEGER NOT NULL,
                end_at TEXT NOT NULL,
                claim_seconds INTEGER NOT NULL,
                ended INTEGER NOT NULL DEFAULT 0,
                message_id INTEGER
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(giveaways)").fetchall()}
        if "message_id" not in columns:
            connection.execute("ALTER TABLE giveaways ADD COLUMN message_id INTEGER")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (giveaway_id, user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                categories TEXT NOT NULL,
                footer TEXT NOT NULL DEFAULT 'Higzen Studio • Select a category below to open a private support ticket.',
                panel_channel_id INTEGER,
                panel_message_id INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                claimed_by INTEGER,
                ticket_code TEXT UNIQUE,
                closing_reason TEXT,
                overall_summary TEXT,
                closed_by INTEGER
            )
            """
        )
        for statement in (
            "ALTER TABLE ticket_config ADD COLUMN footer TEXT NOT NULL DEFAULT 'Higzen Studio • Select a category below to open a private support ticket.'",
            "ALTER TABLE tickets ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
            "ALTER TABLE tickets ADD COLUMN claimed_by INTEGER",
            "ALTER TABLE tickets ADD COLUMN ticket_code TEXT",
            "ALTER TABLE tickets ADD COLUMN closing_reason TEXT",
            "ALTER TABLE tickets ADD COLUMN overall_summary TEXT",
            "ALTER TABLE tickets ADD COLUMN closed_by INTEGER",
            "ALTER TABLE scheduled_messages ADD COLUMN heading TEXT NOT NULL DEFAULT ''",
        ):
            try:
                connection.execute(statement)
            except sqlite3.OperationalError:
                pass
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS welcome_config (
                guild_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                image_url TEXT NOT NULL
            )
            """
        )
        legacy_tickets = connection.execute("SELECT channel_id FROM tickets WHERE ticket_code IS NULL").fetchall()
        for (channel_id,) in legacy_tickets:
            connection.execute("UPDATE tickets SET ticket_code = ? WHERE channel_id = ?", (f"ticket-{secrets.token_hex(4)}", channel_id))
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                product_details TEXT NOT NULL,
                deadline TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                claimed_by INTEGER,
                cancellation_reason TEXT,
                pending_message_id INTEGER,
                storage_message_id INTEGER,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                heading TEXT NOT NULL,
                message TEXT NOT NULL,
                buying_list TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, name COLLATE NOCASE)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS client_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                changed_by INTEGER NOT NULL,
                action TEXT NOT NULL,
                name TEXT NOT NULL,
                heading TEXT NOT NULL,
                message TEXT NOT NULL,
                buying_list TEXT NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )


def default_ticket_categories() -> list[dict[str, str]]:
    return [
        {"name": "Support", "description": "Get help from our support team.", "emoji": "🎫"},
        {"name": "Report a Bug", "description": "Report a server or bot issue.", "emoji": "🐛"},
        {"name": "Other", "description": "Ask anything else.", "emoji": "❓"},
    ]


def get_ticket_config(guild_id: int) -> tuple[str, str, list[dict[str, str]], str, int | None, int | None]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT title, description, categories, footer, panel_channel_id, panel_message_id FROM ticket_config WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
    if row is None:
        return (
            "Higzen Studio | Help & Support Center",
            "Welcome to the support system! If you need assistance, wish to report a bug, or want to appeal a moderation action, you are in the right place.",
            default_ticket_categories(),
            "Higzen Studio • Select a category below to open a private support ticket.",
            None,
            None,
        )
    return row[0], row[1], json.loads(row[2]), row[3], row[4], row[5]


def save_ticket_config(
    guild_id: int,
    title: str,
    description: str,
    categories: list[dict[str, str]],
    footer: str,
    panel_channel_id: int | None = None,
    panel_message_id: int | None = None,
) -> None:
    current = get_ticket_config(guild_id)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            INSERT INTO ticket_config (guild_id, title, description, categories, footer, panel_channel_id, panel_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET title=excluded.title, description=excluded.description,
            categories=excluded.categories, footer=excluded.footer, panel_channel_id=excluded.panel_channel_id, panel_message_id=excluded.panel_message_id
            """,
            (
                guild_id,
                title,
                description,
                json.dumps(categories),
                footer,
                panel_channel_id,
                panel_message_id,
            ),
        )


def get_welcome_config(guild_id: int) -> tuple[str, str, str]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT title, description, image_url FROM welcome_config WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
    if row is None:
        return (
            "Welcome to {server}!",
            "{member}\n\n> **Thanks for joining our Higzen Studio. We’re excited to have you with us. Enjoy your journey!**\n\n**Please make sure to read our rules.**",
            WELCOME_IMAGE_URL or "{avatar}",
        )
    if row[1] in {
        "{member}\n\nThanks for joining our community. We're excited to have you with us. Enjoy your journey!\n\n**Please make sure to read our rules.**",
        "{member}\n\n> Thanks for joining our Swarga Rajyam NETWORK. We're excited to have you with us. Enjoy your journey!\n\n**Please make sure to read our rules.**",
        "{member}\n\n> **Thanks for joining our Sωαrgα Rαjчαm Nᴇᴛᴡᴏʀᴋ. We’re excited to have you with us. Enjoy your journey!**\n\n**Please make sure to read our rules.**",
    }:
        return (
            row[0],
            "{member}\n\n> **Thanks for joining our Higzen Studio. We’re excited to have you with us. Enjoy your journey!**\n\n**Please make sure to read our rules.**",
            row[2],
        )
    return row


def save_welcome_config(guild_id: int, title: str, description: str, image_url: str) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            INSERT INTO welcome_config (guild_id, title, description, image_url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET title=excluded.title,
            description=excluded.description, image_url=excluded.image_url
            """,
            (guild_id, title, description, image_url),
        )


def save_ticket(channel_id: int, guild_id: int, user_id: int, category: str, opener_name: str) -> str:
    code_prefix = re.sub(r"[^a-z0-9]", "", opener_name.lower())[:8] or "ticket"
    with sqlite3.connect(DATABASE_PATH) as connection:
        for _ in range(10):
            ticket_code = f"{code_prefix}-{secrets.randbelow(10000):04d}"
            try:
                connection.execute(
                    "INSERT INTO tickets (channel_id, guild_id, user_id, category, opened_at, ticket_code) VALUES (?, ?, ?, ?, ?, ?)",
                    (channel_id, guild_id, user_id, category, datetime.now(timezone.utc).isoformat(), ticket_code),
                )
                return ticket_code
            except sqlite3.IntegrityError:
                continue
    raise sqlite3.IntegrityError("Could not generate a unique ticket code")


def get_ticket(channel_id: int) -> tuple[int, int, str, str, str, str, int | None] | None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            "SELECT guild_id, user_id, category, opened_at, COALESCE(closed_at, ''), status, claimed_by, ticket_code, closed_by FROM tickets WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()


def update_ticket_state(channel_id: int, status: str | None = None, claimed_by: int | None = None, clear_claim: bool = False) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        if clear_claim:
            connection.execute(
                "UPDATE tickets SET status = COALESCE(?, status), claimed_by = NULL WHERE channel_id = ?",
                (status, channel_id),
            )
        else:
            connection.execute(
                "UPDATE tickets SET status = COALESCE(?, status), claimed_by = COALESCE(?, claimed_by) WHERE channel_id = ?",
                (status, claimed_by, channel_id),
            )


def close_ticket_record(channel_id: int, closing_reason: str, overall_summary: str, closed_by: int, closed_at: str) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE tickets SET closed_at = ?, status = 'closed', closing_reason = ?, overall_summary = ?, closed_by = ? WHERE channel_id = ?",
            (closed_at, closing_reason, overall_summary, closed_by, channel_id),
        )


def get_open_ticket_ids() -> list[int]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute("SELECT channel_id FROM tickets WHERE closed_at IS NULL").fetchall()
    return [row[0] for row in rows]


def create_order(
    guild_id: int,
    requester_id: int,
    username: str,
    product_details: str,
    deadline: str,
) -> int:
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT INTO orders
            (guild_id, requester_id, username, product_details, deadline, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, requester_id, username, product_details, deadline, datetime.now(timezone.utc).isoformat()),
        )
    return int(cursor.lastrowid)


def get_order(order_id: int) -> tuple | None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            """
            SELECT id, guild_id, requester_id, username, product_details, deadline, status,
                   created_at, claimed_by, cancellation_reason, pending_message_id, storage_message_id, completed_at
            FROM orders WHERE id = ?
            """,
            (order_id,),
        ).fetchone()


def update_order_messages(order_id: int, pending_message_id: int | None, storage_message_id: int | None) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE orders SET pending_message_id = ?, storage_message_id = ? WHERE id = ?",
            (pending_message_id, storage_message_id, order_id),
        )


def update_order_status(
    order_id: int,
    status: str,
    claimed_by: int | None = None,
    cancellation_reason: str | None = None,
) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            UPDATE orders SET status = ?, claimed_by = COALESCE(?, claimed_by),
            cancellation_reason = ?, completed_at = ? WHERE id = ?
            """,
            (
                status,
                claimed_by,
                cancellation_reason,
                datetime.now(timezone.utc).isoformat() if status == "completed" else None,
                order_id,
            ),
        )


def get_orders(guild_id: int, status: str | None = None) -> list[tuple]:
    query = "SELECT id, username, product_details, deadline, status, requester_id, claimed_by FROM orders WHERE guild_id = ?"
    parameters: list = [guild_id]
    if status:
        query += " AND status = ?"
        parameters.append(status)
    query += " ORDER BY id DESC"
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(query, parameters).fetchall()


def get_open_order_ids() -> list[int]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            "SELECT id FROM orders WHERE status IN ('pending', 'claimed')"
        ).fetchall()
    return [row[0] for row in rows]


def create_client(
    guild_id: int,
    name: str,
    heading: str,
    message: str,
    buying_list: str,
    changed_by: int,
) -> int | None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE_PATH) as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO clients (guild_id, name, heading, message, buying_list, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, name, heading, message, buying_list, now, now),
            )
        except sqlite3.IntegrityError:
            return None
        client_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO client_history
            (client_id, guild_id, changed_by, action, name, heading, message, buying_list, changed_at)
            VALUES (?, ?, ?, 'created', ?, ?, ?, ?, ?)
            """,
            (client_id, guild_id, changed_by, name, heading, message, buying_list, now),
        )
    return client_id


def find_client(guild_id: int, name: str) -> tuple | None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            """
            SELECT id, guild_id, name, heading, message, buying_list, created_at, updated_at
            FROM clients WHERE guild_id = ? AND name LIKE ? COLLATE NOCASE
            ORDER BY name LIMIT 1
            """,
            (guild_id, f"%{name}%"),
        ).fetchone()


def update_client(
    guild_id: int,
    client_name: str,
    heading: str,
    message: str,
    buying_list: str,
    changed_by: int,
) -> tuple | None:
    client = find_client(guild_id, client_name)
    if client is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE clients SET heading = ?, message = ?, buying_list = ?, updated_at = ? WHERE id = ?",
            (heading, message, buying_list, now, client[0]),
        )
        connection.execute(
            """
            INSERT INTO client_history
            (client_id, guild_id, changed_by, action, name, heading, message, buying_list, changed_at)
            VALUES (?, ?, ?, 'edited', ?, ?, ?, ?, ?)
            """,
            (client[0], guild_id, changed_by, client[2], heading, message, buying_list, now),
        )
    return find_client(guild_id, client[2])


def get_client_history(guild_id: int, client_name: str) -> list[tuple]:
    client = find_client(guild_id, client_name)
    if client is None:
        return []
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            """
            SELECT action, heading, message, buying_list, changed_by, changed_at
            FROM client_history WHERE client_id = ? ORDER BY id DESC
            """,
            (client[0],),
        ).fetchall()


def client_content(client: tuple, title: str = "Client Profile") -> str:
    _, _, name, heading, message, buying_list, created_at, updated_at = client
    updated_time = datetime.fromisoformat(updated_at).astimezone(IST).strftime("%d %B %Y %I:%M %p IST")
    return (
        f"## **{title}**\n\n"
        f"**Name:** {name}\n"
        f"**Heading:** {heading}\n\n"
        f"**Message / Notes:**\n{message}\n\n"
        f"**Buying List:**\n{buying_list}\n\n"
        f"**Last Updated:** {updated_time}"
    )


def get_ticket_panel_guild_ids() -> list[int]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            "SELECT guild_id FROM ticket_config WHERE panel_message_id IS NOT NULL"
        ).fetchall()
    return [row[0] for row in rows]


def get_user_open_ticket(guild_id: int, user_id: int) -> int | None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND closed_at IS NULL",
            (guild_id, user_id),
        ).fetchone()
    return row[0] if row else None


def save_scheduled_message(
    guild_id: int,
    channel_id: int,
    message_type: str,
    content: str,
    heading: str,
    scheduled_at: datetime,
) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT INTO scheduled_messages (guild_id, channel_id, message_type, content, heading, scheduled_at) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_type, content, heading, scheduled_at.isoformat()),
        )


def get_due_messages() -> list[tuple[int, int, str, str, str]]:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            "SELECT id, channel_id, message_type, content, heading FROM scheduled_messages WHERE scheduled_at <= ? ORDER BY scheduled_at",
            (now,),
        ).fetchall()
    return rows


def delete_scheduled_message(message_id: int) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("DELETE FROM scheduled_messages WHERE id = ?", (message_id,))


def parse_duration(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*(s|seconds?|m|minutes?|h|hours?|d|days?)\s*", value.lower())
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)[0]
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * multiplier
    return seconds if 0 < seconds <= 2_592_000 else None


def create_giveaway(
    guild_id: int,
    channel_id: int,
    prize: str,
    description: str,
    winner_count: int,
    host_id: int,
    end_at: datetime,
    claim_seconds: int,
) -> int:
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT INTO giveaways
            (guild_id, channel_id, prize, description, winner_count, host_id, end_at, claim_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, prize, description, winner_count, host_id, end_at.isoformat(), claim_seconds),
        )
    return int(cursor.lastrowid)


def add_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
            (giveaway_id, user_id),
        )
    return cursor.rowcount == 1


def user_has_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        ).fetchone()
    return row is not None


def remove_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
    return cursor.rowcount == 1


def giveaway_has_ended(giveaway_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT ended, end_at FROM giveaways WHERE id = ?",
            (giveaway_id,),
        ).fetchone()
    if row is None:
        return True
    ended, end_at = row
    if ended:
        return True
    return datetime.fromisoformat(end_at) <= datetime.now(timezone.utc)


def get_entry_count(giveaway_id: int) -> int:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
        ).fetchone()
    return int(row[0])


def get_giveaway_winners(giveaway_id: int, winner_count: int) -> list[int]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
        ).fetchall()
    return random.sample([row[0] for row in rows], min(winner_count, len(rows)))


def format_time_remaining(end_at: datetime) -> str:
    remaining_seconds = int((end_at - datetime.now(timezone.utc)).total_seconds())
    if remaining_seconds <= 0:
        return "Ended"
    days, remainder = divmod(remaining_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def build_giveaway_content(
    prize: str,
    description: str,
    winner_count: int,
    host_id: int,
    end_at: str,
    claim_seconds: int,
) -> str:
    end_at_datetime = datetime.fromisoformat(end_at)
    end_time = end_at_datetime.astimezone(IST).strftime("%d %B %Y %H:%M")
    return (
        f"## **{prize}**\n\n{description}\n\n"
        f"**Ends in:** {format_time_remaining(end_at_datetime)}\n"
        f"**Final time:** {end_time}\n"
        f"**Winners:** {winner_count}\n"
        f"**Claim time:** {format_duration(claim_seconds)}\n"
        f"**Hosted by:** <@{host_id}>"
    )


def get_active_giveaway_views() -> list[tuple[int, str]]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            "SELECT id, prize, description, winner_count, host_id, end_at, claim_seconds FROM giveaways WHERE ended = 0"
        ).fetchall()
    views = []
    for giveaway_id, prize, description, winner_count, host_id, end_at, claim_seconds in rows:
        views.append(
            (
                giveaway_id,
                build_giveaway_content(prize, description, winner_count, host_id, end_at, claim_seconds),
            )
        )
    return views


def get_active_giveaway_updates() -> list[tuple[int, int, int, str, str, int, int, str, int]]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """
            SELECT id, channel_id, message_id, prize, description, winner_count, host_id, end_at, claim_seconds
            FROM giveaways
            WHERE ended = 0 AND message_id IS NOT NULL
            ORDER BY end_at
            """
        ).fetchall()
    return rows


def set_giveaway_message_id(giveaway_id: int, message_id: int) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?",
            (message_id, giveaway_id),
        )


def get_due_giveaways() -> list[tuple[int, int, int, int | None, str, str, int, int, str, int]]:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """
            SELECT id, guild_id, channel_id, message_id, prize, description, winner_count, host_id, end_at, claim_seconds
            FROM giveaways WHERE end_at <= ? AND ended = 0 ORDER BY end_at
            """,
            (now,),
        ).fetchall()
        for row in rows:
            connection.execute("UPDATE giveaways SET ended = 1 WHERE id = ?", (row[0],))
    return rows


async def refresh_active_giveaway_messages() -> None:
    giveaways = await asyncio.to_thread(get_active_giveaway_updates)
    for giveaway_id, channel_id, message_id, prize, description, winner_count, host_id, end_at, claim_seconds in giveaways:
        try:
            channel = await bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            message = await channel.fetch_message(message_id)
            content = build_giveaway_content(prize, description, winner_count, host_id, end_at, claim_seconds)
            entry_count = await asyncio.to_thread(get_entry_count, giveaway_id)
            content_with_entries = stylize_message(f"{content}\n**Entries:** {entry_count}")
            await message.edit(content=content_with_entries, view=GiveawayView(giveaway_id, content, entry_count))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning("Skipping refresh for deleted or inaccessible giveaway message %s", message_id)
        except discord.DiscordException:
            logger.exception("Could not refresh giveaway %s", giveaway_id)


async def process_due_giveaways() -> None:
    giveaways = await asyncio.to_thread(get_due_giveaways)
    for giveaway_id, guild_id, channel_id, message_id, prize, description, winner_count, host_id, end_at, claim_seconds in giveaways:
        try:
            channel = await bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            if message_id:
                message = await channel.fetch_message(message_id)
                entry_count = await asyncio.to_thread(get_entry_count, giveaway_id)
                final_content = build_giveaway_content(prize, description, winner_count, host_id, end_at, claim_seconds)
                final_content_with_entries = stylize_message(f"{final_content}\n**Entries:** {entry_count}")
                await message.edit(content=final_content_with_entries, view=GiveawayView(giveaway_id, final_content, entry_count))
            winner_ids = await asyncio.to_thread(get_giveaway_winners, giveaway_id, winner_count)
            winner_tags = ", ".join(f"<@{user_id}>" for user_id in winner_ids) or "No valid entries"
            ended_at = datetime.fromisoformat(end_at).astimezone(IST).strftime("%d %B %Y %H:%M")
            claim_time = format_duration(claim_seconds)
            content = (
                f"🎉 **Congratulations to {winner_tags}!** 🎉\n\n"
                f"**Prize:** {prize}\n"
                f"**Claim within:** {claim_time}\n"
                f"**Hosted by:** <@{host_id}>\n"
                f"**Ended:** {ended_at}"
            )
            await channel.send(
                view=create_message_container(
                    "giveaway winner",
                    content,
                    heading="<:BOOOO:1549096753196957706> Giveaway Winner",
                )
            )
        except discord.DiscordException:
            logger.exception("Could not finish giveaway %s", giveaway_id)


def format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} day(s)"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour(s)"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute(s)"
    return f"{seconds} second(s)"


def create_message_container(message_type: str, content: str, heading: str = "") -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    title = stylize_message(heading.strip() or message_type.title())
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(stylize_message(f"## **{title}**")),
            discord.ui.Separator(),
            discord.ui.TextDisplay(stylize_message(content)),
        )
    )
    return view


async def send_container_message(channel: discord.TextChannel, message_type: str, content: str, heading: str = "") -> None:
    await channel.send(view=create_message_container(message_type, content, heading))


async def find_audit_actor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int | None = None,
) -> str:
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            entry_target_id = getattr(entry.target, "id", None)
            if target_id is None or entry_target_id == target_id:
                return str(entry.user)
    except discord.DiscordException:
        return "Unknown moderator"
    return "Unknown moderator"


async def send_server_log(
    guild: discord.Guild,
    title: str,
    details: str,
    action: discord.AuditLogAction | None = None,
    target_id: int | None = None,
) -> None:
    log_channel = await fetch_text_channel(SERVER_LOG_CHANNEL_ID)
    if log_channel is None:
        return
    actor = await find_audit_actor(guild, action, target_id) if action else "Discord event"
    content = f"## **{title}**\n\n{details}\n\n**Changed By:** {actor}"
    try:
        await log_channel.send(view=create_message_container("server change", content))
    except discord.DiscordException:
        logger.exception("Could not send server change log")


def channel_change_details(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> str:
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` → `{after.name}`")
    if before.category_id != after.category_id:
        changes.append(f"**Category:** `{before.category_id}` → `{after.category_id}`")
    if before.position != after.position:
        changes.append(f"**Position:** `{before.position}` → `{after.position}`")
    return "\n".join(changes) or "**Details:** Channel settings or permissions changed."


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    await send_server_log(
        channel.guild,
        "Channel Created",
        f"**Channel:** {channel.mention}\n**Type:** `{channel.type}`",
        discord.AuditLogAction.channel_create,
        channel.id,
    )


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    await send_server_log(
        channel.guild,
        "Channel Deleted",
        f"**Channel:** `#{channel.name}`\n**Type:** `{channel.type}`",
        discord.AuditLogAction.channel_delete,
        channel.id,
    )


@bot.event
async def on_guild_channel_update(
    before: discord.abc.GuildChannel,
    after: discord.abc.GuildChannel,
) -> None:
    await send_server_log(
        after.guild,
        "Channel Updated",
        f"**Channel:** {after.mention}\n{channel_change_details(before, after)}",
        discord.AuditLogAction.channel_update,
        after.id,
    )


@bot.event
async def on_guild_role_create(role: discord.Role) -> None:
    await send_server_log(
        role.guild,
        "Role Created",
        f"**Role:** {role.mention}\n**Color:** `{role.color}`",
        discord.AuditLogAction.role_create,
        role.id,
    )


@bot.event
async def on_guild_role_delete(role: discord.Role) -> None:
    await send_server_log(
        role.guild,
        "Role Deleted",
        f"**Role:** `{role.name}`\n**Role ID:** `{role.id}`",
        discord.AuditLogAction.role_delete,
        role.id,
    )


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` → `{after.name}`")
    if before.permissions != after.permissions:
        changes.append("**Permissions:** Role permissions changed.")
    if before.colour != after.colour:
        changes.append(f"**Color:** `{before.colour}` → `{after.colour}`")
    await send_server_log(
        after.guild,
        "Role Updated",
        f"**Role:** {after.mention}\n" + ("\n".join(changes) or "**Details:** Role settings changed."),
        discord.AuditLogAction.role_update,
        after.id,
    )


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` → `{after.name}`")
    if before.description != after.description:
        changes.append("**Description:** Server description changed.")
    if before.verification_level != after.verification_level:
        changes.append(f"**Verification:** `{before.verification_level}` → `{after.verification_level}`")
    await send_server_log(
        after,
        "Server Updated",
        "\n".join(changes) or "**Details:** Server settings changed.",
        discord.AuditLogAction.guild_update,
        after.id,
    )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    changes = []
    if before.nick != after.nick:
        changes.append(f"**Nickname:** `{before.nick}` → `{after.nick}`")
    before_roles = {role.id for role in before.roles}
    after_roles = {role.id for role in after.roles}
    added_roles = [role.mention for role in after.roles if role.id not in before_roles]
    removed_roles = [role.name for role in before.roles if role.id not in after_roles]
    if added_roles:
        changes.append(f"**Roles Added:** {', '.join(added_roles)}")
    if removed_roles:
        changes.append(f"**Roles Removed:** {', '.join(removed_roles)}")
    if changes:
        await send_server_log(
            after.guild,
            "Member Updated",
            f"**Member:** {after.mention}\n" + "\n".join(changes),
            discord.AuditLogAction.member_role_update,
            after.id,
        )


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if AUTO_ROLE_ID:
        role = member.guild.get_role(int(AUTO_ROLE_ID))
        if role is None:
            logger.warning("AUTO_ROLE_ID %s was not found", AUTO_ROLE_ID)
        elif role >= member.guild.me.top_role:
            logger.warning("Bot cannot assign role %s because it is above the bot's top role", role.name)
        else:
            try:
                await member.add_roles(role, reason="Automatic role for new member")
            except discord.Forbidden:
                logger.exception("Missing permission to assign role %s", role.name)
            except discord.HTTPException:
                logger.exception("Failed to assign role %s", role.name)

    await send_welcome(member)
    await send_server_log(member.guild, "Member Joined", f"**Member:** {member.mention}\n**Account:** `{member}`")


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    await send_server_log(member.guild, "Member Left", f"**Member:** `{member}`\n**User ID:** `{member.id}`")


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    await send_server_log(
        guild,
        "Member Banned",
        f"**Member:** `{user}`\n**User ID:** `{user.id}`",
        discord.AuditLogAction.ban,
        user.id,
    )


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    await send_server_log(
        guild,
        "Member Unbanned",
        f"**Member:** `{user}`\n**User ID:** `{user.id}`",
        discord.AuditLogAction.unban,
        user.id,
    )


@bot.event
async def on_guild_emojis_update(
    guild: discord.Guild,
    before: tuple[discord.Emoji, ...],
    after: tuple[discord.Emoji, ...],
) -> None:
    await send_server_log(
        guild,
        "Server Emojis Updated",
        f"**Before:** `{len(before)}` emojis\n**After:** `{len(after)}` emojis",
    )


@bot.event
async def on_guild_stickers_update(
    guild: discord.Guild,
    before: tuple[discord.GuildSticker, ...],
    after: tuple[discord.GuildSticker, ...],
) -> None:
    await send_server_log(
        guild,
        "Server Stickers Updated",
        f"**Before:** `{len(before)}` stickers\n**After:** `{len(after)}` stickers",
    )


@bot.event
async def on_webhooks_update(channel: discord.abc.GuildChannel) -> None:
    await send_server_log(channel.guild, "Webhooks Updated", f"**Channel:** {channel.mention}")


@bot.event
async def on_thread_create(thread: discord.Thread) -> None:
    await send_server_log(thread.guild, "Thread Created", f"**Thread:** {thread.mention}", discord.AuditLogAction.thread_create, thread.id)


@bot.event
async def on_thread_delete(thread: discord.Thread) -> None:
    await send_server_log(thread.guild, "Thread Deleted", f"**Thread:** `{thread.name}`", discord.AuditLogAction.thread_delete, thread.id)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.author.bot or before.content == after.content or before.guild is None:
        return
    await send_server_log(
        before.guild,
        "Message Edited",
        f"**Author:** {before.author.mention}\n**Channel:** {before.channel.mention}\n**Before:** {before.content[:1500]}\n**After:** {after.content[:1500]}",
    )


@bot.event
async def on_message_delete(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return
    await send_server_log(
        message.guild,
        "Message Deleted",
        f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n**Content:** {message.content[:2000] or '[no cached text]'}",
        discord.AuditLogAction.message_delete,
        message.id,
    )


def order_content(order: tuple, status_override: str | None = None, reason: str | None = None) -> str:
    (
        order_id,
        guild_id,
        requester_id,
        username,
        product_details,
        deadline,
        status,
        created_at,
        claimed_by,
        cancellation_reason,
        pending_message_id,
        storage_message_id,
        completed_at,
    ) = order
    status = status_override or status
    guild = bot.get_guild(guild_id)
    status_label = {
        "pending": "⏳ Pending",
        "claimed": "🛠️ Claimed",
        "cancelled": f"{server_emoji(guild, 'xx_', '❌')} Cancelled",
        "completed": f"{server_emoji(guild, 'tickk', '✅')} Completed",
    }.get(status, status.title())
    created_time = datetime.fromisoformat(created_at).astimezone(IST).strftime("%d %B %Y %I:%M %p IST")
    content = (
        f"## **Order #{order_id}**\n\n"
        f"**Username:** {username}\n"
        f"**Product Details:**\n{product_details}\n\n"
        f"**Deadline:** {deadline}\n"
        f"**Status:** {status_label}\n"
        f"**Placed:** {created_time}"
    )
    if claimed_by:
        content += f"\n**Claimed By:** <@{claimed_by}>"
    if reason or cancellation_reason:
        content += f"\n**Cancellation Reason:** {reason or cancellation_reason}"
    if completed_at:
        completed_time = datetime.fromisoformat(completed_at).astimezone(IST).strftime("%d %B %Y %I:%M %p IST")
        content += f"\n**Work Done:** {completed_time}"
    return content


def is_order_mod(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.manage_channels:
        return True
    return bool(ORDER_MOD_ROLE_ID and int(ORDER_MOD_ROLE_ID) in {role.id for role in interaction.user.roles})


async def fetch_text_channel(channel_id: str | None) -> discord.TextChannel | None:
    if not channel_id:
        return None
    channel = bot.get_channel(int(channel_id))
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        channel = await bot.fetch_channel(int(channel_id))
    except discord.DiscordException:
        return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def edit_order_message(channel_id: str | None, message_id: int | None, order: tuple, manage: bool = False) -> None:
    if not message_id:
        return
    channel = await fetch_text_channel(str(channel_id))
    if channel is None:
        return
    try:
        message = await channel.fetch_message(message_id)
        view = OrderView(order[0], order_content(order)) if manage else create_message_container("order", order_content(order))
        await message.edit(view=view)
    except discord.DiscordException:
        logger.exception("Could not update order message %s", message_id)


class OrderCancelModal(discord.ui.Modal, title="Cancel Order"):
    reason = discord.ui.TextInput(
        label="Cancellation reason *",
        placeholder="Explain why this order is being cancelled.",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, order_id: int) -> None:
        super().__init__()
        self.order_id = order_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not is_order_mod(interaction):
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(bold_message(f"{wrong_emoji} Only moderators can cancel orders."), ephemeral=True)
            return
        order = await asyncio.to_thread(get_order, self.order_id)
        if order is None or order[6] in {"cancelled", "completed"}:
            await interaction.response.send_message(bold_message("This order is already closed."), ephemeral=True)
            return
        await asyncio.to_thread(update_order_status, self.order_id, "cancelled", None, self.reason.value)
        order = await asyncio.to_thread(get_order, self.order_id)
        await edit_order_message(ORDER_STORAGE_CHANNEL_ID, order[11], order, manage=True)
        await edit_order_message(ORDER_PENDING_CHANNEL_ID, order[10], order)
        wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
        await interaction.response.send_message(bold_message(f"{wrong_emoji} Order cancelled and recorded."))


class OrderView(discord.ui.LayoutView):
    def __init__(self, order_id: int, content: str | None = None) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        if content is None:
            order = get_order(order_id)
            content = order_content(order) if order else "## **Manage Order**"
        claim = discord.ui.Button(label="Claim", style=discord.ButtonStyle.success, custom_id=f"order:claim:{order_id}")
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id=f"order:cancel:{order_id}")
        complete = discord.ui.Button(label="Completed", style=discord.ButtonStyle.primary, custom_id=f"order:complete:{order_id}")
        claim.callback = self.claim_callback
        cancel.callback = self.cancel_callback
        complete.callback = self.complete_callback
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(stylize_message(content)),
                discord.ui.Separator(),
                discord.ui.ActionRow(claim, cancel, complete),
            )
        )

    async def claim_callback(self, interaction: discord.Interaction) -> None:
        if not is_order_mod(interaction):
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(bold_message(f"{wrong_emoji} Only moderators can claim orders."), ephemeral=True)
            return
        order = await asyncio.to_thread(get_order, self.order_id)
        if order is None or order[6] != "pending":
            await interaction.response.send_message(bold_message("This order is no longer pending."), ephemeral=True)
            return
        await asyncio.to_thread(update_order_status, self.order_id, "claimed", interaction.user.id)
        order = await asyncio.to_thread(get_order, self.order_id)
        await edit_order_message(ORDER_STORAGE_CHANNEL_ID, order[11], order, manage=True)
        await edit_order_message(ORDER_PENDING_CHANNEL_ID, order[10], order)
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Order #{self.order_id} claimed by {interaction.user.mention}."))

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        if not is_order_mod(interaction):
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(bold_message(f"{wrong_emoji} Only moderators can cancel orders."), ephemeral=True)
            return
        await interaction.response.send_modal(OrderCancelModal(self.order_id))

    async def complete_callback(self, interaction: discord.Interaction) -> None:
        if not is_order_mod(interaction):
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(bold_message(f"{wrong_emoji} Only moderators can complete orders."), ephemeral=True)
            return
        order = await asyncio.to_thread(get_order, self.order_id)
        if order is None or order[6] in {"cancelled", "completed"}:
            await interaction.response.send_message(bold_message("This order is already closed."), ephemeral=True)
            return
        await asyncio.to_thread(update_order_status, self.order_id, "completed")
        order = await asyncio.to_thread(get_order, self.order_id)
        completed_channel = await fetch_text_channel(ORDER_COMPLETED_CHANNEL_ID)
        if completed_channel:
            await completed_channel.send(view=create_message_container("completed order", order_content(order)))
        for message_channel_id, message_id in ((ORDER_PENDING_CHANNEL_ID, order[10]), (ORDER_STORAGE_CHANNEL_ID, order[11])):
            if message_id:
                channel = await fetch_text_channel(str(message_channel_id))
                if channel:
                    try:
                        await (await channel.fetch_message(message_id)).delete()
                    except discord.DiscordException:
                        logger.exception("Could not remove order message %s", message_id)
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Order #{self.order_id} completed and moved to completed orders."))


class OrderModal(discord.ui.Modal, title="Create Pending Order"):
    username = discord.ui.TextInput(label="Username *", placeholder="Customer username", max_length=100)
    product_details = discord.ui.TextInput(
        label="Product details *", placeholder="Describe the product or work required.", style=discord.TextStyle.paragraph, max_length=2000
    )
    deadline = discord.ui.TextInput(label="Deadline time *", placeholder="Example: 10 September 2026, 8:00 PM IST", max_length=100)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pending_channel = await fetch_text_channel(ORDER_PENDING_CHANNEL_ID)
        storage_channel = await fetch_text_channel(ORDER_STORAGE_CHANNEL_ID)
        if not isinstance(interaction.guild, discord.Guild) or pending_channel is None or storage_channel is None:
            await interaction.response.send_message(bold_message("Order channels are not configured correctly."), ephemeral=True)
            return
        order_id = await asyncio.to_thread(
            create_order,
            interaction.guild.id,
            interaction.user.id,
            self.username.value,
            self.product_details.value,
            self.deadline.value,
        )
        order = await asyncio.to_thread(get_order, order_id)
        pending_ping = f"{interaction.user.mention} {f'<@&{ORDER_MOD_ROLE_ID}>' if ORDER_MOD_ROLE_ID else ''}"
        await pending_channel.send(content=pending_ping)
        pending_message = await pending_channel.send(view=create_message_container("pending order", order_content(order)))
        storage_message = await storage_channel.send(view=OrderView(order_id, order_content(order)))
        await asyncio.to_thread(update_order_messages, order_id, pending_message.id, storage_message.id)
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Order #{order_id} added to pending orders."), ephemeral=True)


def parse_custom_emoji(value: str) -> discord.PartialEmoji | str:
    value = value.strip()
    if value.startswith("<"):
        try:
            return discord.PartialEmoji.from_str(value)
        except ValueError:
            pass
    return value


def server_emoji(guild: discord.Guild | None, name: str, fallback: str) -> str:
    if guild is None:
        return fallback

    requested_name = name.casefold().strip()
    base_name = requested_name.strip("_")
    candidates = []

    for candidate in [requested_name, base_name]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    if base_name.startswith("tick"):
        for candidate in ["tick", "tickt", "tickk", "ticket", "check", "checkmark"]:
            if candidate not in candidates:
                candidates.append(candidate)

    if base_name.startswith("xx"):
        for candidate in ["xx", "xx_", "cross", "cancel", "wrong"]:
            if candidate not in candidates:
                candidates.append(candidate)

    if base_name == "pin":
        for candidate in ["pin", "pinnnn", "pinned", "note"]:
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        emoji = next((item for item in guild.emojis if item.name.casefold() == candidate), None)
        if emoji:
            return str(emoji)

    normalized_requested = re.sub(r"[^a-z0-9]+", "", requested_name)
    if normalized_requested:
        for item in guild.emojis:
            normalized_item = re.sub(r"[^a-z0-9]+", "", item.name.casefold())
            if normalized_requested in normalized_item or normalized_item in normalized_requested:
                return str(item)

    return fallback


def interaction_emoji(interaction: discord.Interaction, name: str, fallback: str) -> str:
    return server_emoji(interaction.guild, name, fallback)


STYLIZED_LETTERS = str.maketrans(
    {
        "a": "α",
        "b": "ʙ",
        "c": "ᴄ",
        "d": "ᴅ",
        "e": "ᴇ",
        "f": "ꜰ",
        "g": "ɢ",
        "h": "ʜ",
        "i": "ɪ",
        "j": "ᴊ",
        "k": "ᴋ",
        "l": "ʟ",
        "m": "ᴍ",
        "n": "ɴ",
        "o": "ᴏ",
        "p": "ᴘ",
        "q": "ǫ",
        "r": "ʀ",
        "s": "s",
        "t": "ᴛ",
        "u": "ᴜ",
        "v": "ᴠ",
        "w": "ω",
        "x": "x",
        "y": "ʏ",
        "z": "ᴢ",
    }
)


def stylize_message(content: str) -> str:
    token_pattern = re.compile(r"(`[^`]*`|<:[^>]+>|<#\d+>|<@&?\d+>)")
    parts = token_pattern.split(content)

    def _should_keep(part: str) -> bool:
        return bool(part) and (
            part.startswith("`") and part.endswith("`")
            or bool(re.fullmatch(r"<:[^>]+>|<#\d+>|<@&?\d+>", part))
        )

    return "".join(part if _should_keep(part) else part.translate(STYLIZED_LETTERS) for part in parts)


def bold_message(content: str) -> str:
    return f"**{stylize_message(content)}**"


def ticket_panel_content(title: str, description: str) -> tuple[str, str]:
    return (
        f"## **{title}**",
        f"> {description}\n\n"
        "**How to use:**\n"
        "- Choose the category that best fits your issue from the menu below.\n"
        "- A private ticket channel will be opened for you.\n"
        "- Provide all relevant details, and our staff will assist you."
    )


def ticket_panel_footer(summary_emoji: str) -> str:
    return f"-# {summary_emoji} **Note:** Abuse of the ticket system may result in restrictions."


class TicketPanelView(discord.ui.LayoutView):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=None)
        title, description, categories, _, _, _ = get_ticket_config(guild_id)
        ticket_emoji = server_emoji(bot.get_guild(guild_id), "tickt", "🎫")
        summary_emoji = server_emoji(bot.get_guild(guild_id), "pin", "📌")
        footer = ticket_panel_footer(summary_emoji)
        options = [
            discord.SelectOption(
                label=category["name"][:100],
                description=category["description"][:100],
                value=category["name"][:100],
                emoji=parse_custom_emoji(
                    ticket_emoji if category.get("emoji", "🎫") == "🎫" else category["emoji"]
                ),
            )
            for category in categories[:25]
        ]
        select = discord.ui.Select(
            placeholder="Select a ticket category...",
            custom_id=f"ticket:category:{guild_id}",
            options=options,
        )
        select.callback = self.category_callback
        self.guild_id = guild_id
        panel_heading, panel_description = ticket_panel_content(title, description)
        self.add_item(
            discord.ui.Container(
            discord.ui.TextDisplay(stylize_message(panel_heading)),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(stylize_message(panel_description)),
            discord.ui.Separator(),
                discord.ui.ActionRow(select),
                discord.ui.Separator(),
                discord.ui.TextDisplay(stylize_message(footer)),
            )
        )

    async def category_callback(self, interaction: discord.Interaction) -> None:
        selected_values = interaction.data.get("values", []) if interaction.data else []
        if not interaction.guild or not selected_values:
            return
        category_name = str(selected_values[0])
        _, _, categories, _, _, _ = await asyncio.to_thread(get_ticket_config, interaction.guild.id)
        selected_category = next(
            (category for category in categories if category["name"] == category_name),
            None,
        )
        if selected_category and selected_category.get("frozen_at"):
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} The ticket system for this category is not available because of high traffic. Please try again later."),
                ephemeral=True,
            )
            return
        ticket_emoji = server_emoji(interaction.guild, "tickt", "🎫")
        await interaction.response.send_message(bold_message(f"{ticket_emoji} Creating your ticket..."), ephemeral=True)
        if isinstance(interaction.message, discord.Message):
            try:
                await interaction.message.edit(view=TicketPanelView(self.guild_id))
            except discord.DiscordException:
                logger.exception("Could not reset ticket panel after category selection")
        existing_channel_id = await asyncio.to_thread(
            get_user_open_ticket, interaction.guild.id, interaction.user.id
        )
        if existing_channel_id:
            await interaction.edit_original_response(
                content=bold_message(f"You already have an open ticket: <#{existing_channel_id}>"),
            )
            return

        parent = None
        if TICKET_CATEGORY_ID:
            try:
                category_id = int(TICKET_CATEGORY_ID)
                parent = interaction.guild.get_channel(category_id)
                if parent is None:
                    parent = await bot.fetch_channel(category_id)
            except ValueError:
                await interaction.edit_original_response(
                    content=bold_message(f"{interaction_emoji(interaction, 'xx_', '❌')} TICKET_CATEGORY_ID is not a valid Discord channel ID.")
                )
                return
            if not isinstance(parent, discord.CategoryChannel):
                await interaction.edit_original_response(
                    content=bold_message(f"{interaction_emoji(interaction, 'xx_', '❌')} TICKET_CATEGORY_ID does not point to a server category. Check your .env file.")
                )
                return
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if TICKET_MOD_ROLE_ID:
            try:
                mod_role = interaction.guild.get_role(int(TICKET_MOD_ROLE_ID))
            except ValueError:
                await interaction.edit_original_response(
                    content=bold_message(f"{interaction_emoji(interaction, 'xx_', '❌')} TICKET_MOD_ROLE_ID is not a valid Discord role ID.")
                )
                return
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        try:
            channel = await interaction.guild.create_text_channel(
                name=f"ticket-{interaction.user.name}"[:100],
                category=parent,
                overwrites=overwrites,
                reason=f"Ticket opened by {interaction.user}",
            )
            ticket_code = await asyncio.to_thread(
                save_ticket,
                channel.id,
                interaction.guild.id,
                interaction.user.id,
                category_name,
                interaction.user.name,
            )
            await channel.send(
                content=f"{interaction.user.mention} {f'<@&{TICKET_MOD_ROLE_ID}>' if TICKET_MOD_ROLE_ID else ''}"
            )
            await channel.send(view=TicketView(channel.id))
        except (discord.Forbidden, discord.HTTPException, sqlite3.Error):
            logger.exception("Could not create ticket for %s", interaction.user)
            await interaction.edit_original_response(
                content=bold_message(f"{interaction_emoji(interaction, 'xx_', '❌')} Ticket could not be created. Please contact a moderator or try again later.")
            )
            return
        await interaction.edit_original_response(
            content=bold_message(f"{interaction_emoji(interaction, 'tickk', '✅')} Ticket created successfully: {channel.mention} (`{ticket_code}`)")
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        logger.exception("Ticket panel interaction failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(bold_message(f"{interaction_emoji(interaction, 'xx_', '❌')} Ticket creation failed. Please contact a moderator or try again later."), ephemeral=True)
        else:
            await interaction.response.send_message(bold_message(f"{interaction_emoji(interaction, 'xx_', '❌')} Ticket creation failed. Please contact a moderator or try again later."), ephemeral=True)


async def refresh_ticket_panel(guild_id: int) -> bool:
    _, _, _, _, panel_channel_id, panel_message_id = await asyncio.to_thread(get_ticket_config, guild_id)
    if not panel_channel_id or not panel_message_id:
        return False
    channel = await fetch_text_channel(str(panel_channel_id))
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(panel_message_id)
        await message.edit(view=TicketPanelView(guild_id))
        return True
    except discord.DiscordException:
        logger.exception("Could not update ticket panel %s", panel_message_id)
        return False


def create_ticket_status_container(
    title: str,
    overview: str,
    administrative_details: str,
    notice: str,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    heading = f"## **{title}**"
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(stylize_message(heading)),
            discord.ui.Separator(),
            discord.ui.TextDisplay(stylize_message(overview)),
            discord.ui.Separator(),
            discord.ui.TextDisplay(stylize_message(administrative_details)),
            discord.ui.Separator(),
            discord.ui.TextDisplay(stylize_message(notice)),
        )
    )
    return view


async def send_category_announcement(guild: discord.Guild, content: str) -> None:
    channel = await fetch_text_channel(ANNOUNCEMENT_CHANNEL_ID)
    if channel is None:
        logger.warning("ANNOUNCEMENT_CHANNEL_ID is not configured or is invalid")
        return
    try:
        view = discord.ui.LayoutView()
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(stylize_message(content))))
        await channel.send(view=view)
    except discord.DiscordException:
        logger.exception("Could not send ticket category announcement")


class TicketView(discord.ui.LayoutView):
    def __init__(self, channel_id: int) -> None:
        super().__init__(timeout=None)
        self.channel_id = channel_id
        ticket = get_ticket(channel_id)
        claim = discord.ui.Button(
            label="Release Claim" if ticket and ticket[6] else "Claim Ticket",
            style=discord.ButtonStyle.secondary if ticket and ticket[6] else discord.ButtonStyle.success,
            custom_id=f"ticket:claim:{channel_id}",
        )
        add_member = discord.ui.Button(label="Add Member", style=discord.ButtonStyle.secondary, custom_id=f"ticket:add:{channel_id}")
        hold = discord.ui.Button(
            label="Take Off Hold" if ticket and ticket[5] == "held" else "Put on Hold",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket:hold:{channel_id}",
        )
        close = discord.ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id=f"ticket:close:{channel_id}")
        claim.callback = self.claim_callback
        add_member.callback = self.add_member_callback
        hold.callback = self.hold_callback
        close.callback = self.close_callback
        created_time = (
            datetime.fromisoformat(ticket[3]).astimezone(IST).strftime("%d %B %Y %I:%M %p IST")
            if ticket else "Unknown"
        )
        owner = f"<@{ticket[1]}>" if ticket else "Unknown"
        ticket_code = ticket[7] if ticket and ticket[7] else "ticket-unknown"
        claim_status = f"**Claimed By:** <@{ticket[6]}>\n" if ticket and ticket[6] else "**Claimed By:** Nobody\n"
        ticket_emoji = server_emoji(bot.get_guild(ticket[0]) if ticket else None, "tickt", "🎫")
        ticket_content = (
            f"## **{ticket_emoji} Higzen Studio Support Ticket**\n\n"
            f"**Ticket ID:** `{ticket_code}`\n"
            f"**Created By:** {owner}\n"
            f"**Category:** {ticket[2] if ticket else 'Support'}\n"
            f"{claim_status}\n"
            "> *Please describe your issue as much detail as possible. Our support staff will review your request and reply shortly.*"
        )
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(stylize_message(ticket_content)),
                discord.ui.Separator(),
                discord.ui.ActionRow(claim, add_member, hold, close),
            )
        )

    async def require_mod(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.manage_channels:
            return True
        if TICKET_MOD_ROLE_ID and int(TICKET_MOD_ROLE_ID) in [role.id for role in interaction.user.roles]:
            return True
        wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
        await interaction.response.send_message(bold_message(f"{wrong_emoji} Only staff can use this ticket control."), ephemeral=True)
        return False

    async def claim_callback(self, interaction: discord.Interaction) -> None:
        if not await self.require_mod(interaction):
            return
        ticket = await asyncio.to_thread(get_ticket, self.channel_id)
        if ticket and ticket[6]:
            await asyncio.to_thread(update_ticket_state, self.channel_id, None, None, True)
            message = f"{interaction_emoji(interaction, 'tickk', '✅')} Ticket claim released."
        else:
            await asyncio.to_thread(update_ticket_state, self.channel_id, None, interaction.user.id)
            message = f"{interaction_emoji(interaction, 'tickk', '✅')} Ticket claimed by {interaction.user.mention}."
        if isinstance(interaction.message, discord.Message):
            await interaction.message.edit(view=TicketView(self.channel_id))
        await interaction.response.send_message(bold_message(message))

    async def add_member_callback(self, interaction: discord.Interaction) -> None:
        if not await self.require_mod(interaction):
            return
        await interaction.response.send_message(bold_message("Use `/ticketadd @member` to add a member to this ticket."), ephemeral=True)

    async def hold_callback(self, interaction: discord.Interaction) -> None:
        if not await self.require_mod(interaction):
            return
        ticket = await asyncio.to_thread(get_ticket, self.channel_id)
        is_held = bool(ticket and ticket[5] == "held")
        if isinstance(interaction.channel, discord.TextChannel):
            base_name = interaction.channel.name.removeprefix("on-hold-").removeprefix("ticket-")
            await interaction.channel.edit(name=f"ticket-{base_name}" if is_held else f"on-hold-{base_name}")
        await asyncio.to_thread(update_ticket_state, self.channel_id, "open" if is_held else "held")
        if isinstance(interaction.message, discord.Message):
            await interaction.message.edit(view=TicketView(self.channel_id))
        await interaction.response.send_message(bold_message("▶️ Ticket is active again." if is_held else "⏸️ Ticket put on hold."))

    async def close_callback(self, interaction: discord.Interaction) -> None:
        if not await self.require_mod(interaction):
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.response.send_modal(TicketCloseModal(self.channel_id))


class TicketCloseModal(discord.ui.Modal, title="Close Ticket"):
    closing_reason = discord.ui.TextInput(
        label="Closing reason",
        placeholder="Why is this ticket being closed?",
        max_length=1000,
        style=discord.TextStyle.paragraph,
    )
    overall_summary = discord.ui.TextInput(
        label="Overall summary",
        placeholder="Summarize the issue and its resolution.",
        max_length=2000,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, channel_id: int) -> None:
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(bold_message(f"{wrong_emoji} This ticket channel is no longer available."), ephemeral=True)
            return
        await interaction.response.defer()
        reason = self.closing_reason.value.strip()
        summary = self.overall_summary.value.strip()
        closed_at = datetime.now(timezone.utc).isoformat()
        await create_ticket_transcript(interaction.channel, reason, summary, interaction.user.id, closed_at)
        await asyncio.to_thread(close_ticket_record, interaction.channel.id, reason, summary, interaction.user.id, closed_at)
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.followup.send(bold_message(f"{correct_emoji} Ticket closed. The JSON transcript was sent to the log channel and ticket owner."))
        await asyncio.sleep(2)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")


def create_ticket_archive_container(content: str, filename: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    heading, body = content.split("\n", 1)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(stylize_message(heading)),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(stylize_message(body)),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.File(f"attachment://{filename}"),
        )
    )
    return view


def ticket_time(value: str | None) -> str:
    if not value:
        return "Unknown"
    return datetime.fromisoformat(value).astimezone(IST).strftime("%d %B %Y, %I:%M %p IST")


def build_ticket_html(ticket: tuple, messages: list[dict], closing_reason: str, overall_summary: str) -> str:
    ticket_code = ticket[7] or "ticket-unknown"
    message_rows = "\n".join(
        f'<article><img class="avatar" src="{html.escape(message["avatar_url"], quote=True)}" alt="">'
        f'<div><strong>{html.escape(message["author"])}</strong> '
        f'<time>{html.escape(ticket_time(message["created_at"]))}</time>'
        f'<p>{html.escape(message["content"]) or "<em>(no text)</em>"}</p>'
        + "".join(f'<a href="{html.escape(url, quote=True)}">Attachment</a> ' for url in message["attachments"])
        + "</div></article>"
        for message in messages
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(ticket_code)}</title>
<style>body{{font:15px system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 20px;background:#313338;color:#dbdee1}}header{{border-bottom:1px solid #1e1f22;padding-bottom:16px}}h1{{margin:0;color:#f2f3f5}}header p{{color:#949ba4}}.details{{background:#2b2d31;border:1px solid #1e1f22;padding:16px;margin:20px 0;border-radius:6px}}article{{display:flex;gap:12px;border-bottom:1px solid #3f4147;padding:14px 0}}.avatar{{width:40px;height:40px;border-radius:50%;object-fit:cover}}time{{color:#949ba4;font-size:12px;margin-left:8px}}p{{white-space:pre-wrap;margin:8px 0}}a{{color:#00a8fc}}</style></head>
<body><header><h1>{html.escape(ticket_code)}</h1><p>Higzen Studio ticket transcript</p></header>
<section class="details"><p><strong>Closing Reason:</strong> {html.escape(closing_reason)}</p>
<p><strong>Summary:</strong> {html.escape(overall_summary)}</p></section><h2>Conversation</h2>{message_rows}
</body></html>"""


async def create_ticket_transcript(
    channel: discord.TextChannel,
    closing_reason: str,
    overall_summary: str,
    closed_by: int,
    closed_at: str,
) -> None:
    ticket = await asyncio.to_thread(get_ticket, channel.id)
    messages = []
    async for message in channel.history(limit=None, oldest_first=True):
        messages.append({
            "id": message.id,
            "author_id": message.author.id,
            "author": str(message.author),
            "content": message.content,
            "created_at": message.created_at.isoformat(),
            "avatar_url": str(message.author.display_avatar.url),
            "attachments": [attachment.url for attachment in message.attachments],
        })
    if not ticket:
        logger.warning("Ticket record %s could not be found", channel.id)
        return
    ticket_code = ticket[7] or f"ticket-{secrets.token_hex(4)}"
    file_data = io.BytesIO(build_ticket_html(ticket, messages, closing_reason, overall_summary).encode("utf-8"))
    filename = f"transcript-{ticket_code}.html"
    transcript_size = len(file_data.getvalue()) / 1024
    claimed_user = f"`<@{ticket[6]}>`" if ticket[6] else "`[Nobody]`"
    summary_emoji = server_emoji(bot.get_guild(ticket[0]), "pin", "📌")
    log_text = (
        "## 🧾 **HIGZEN STUDIO | TICKET CLOSED**\n"
        f"👋 Hello <@{ticket[1]}>, your ticket has been closed.\n\n"
        "📁 **Ticket Details:**\n"
        f"• Ticket Number: `{ticket_code}`\n"
        f"• Opened User: <@{ticket[1]}>\n"
        f"• Claimed User: {claimed_user}\n"
        f"• Closed User: `<@{closed_by}>`\n\n"
        "🕒 **Timestamps (IST):**\n"
        f"• Opened At: `{datetime.fromisoformat(ticket[3]).astimezone(IST).strftime('%d %b %Y, %I:%M %p')}`\n"
        f"• Closed At: `{datetime.fromisoformat(closed_at).astimezone(IST).strftime('%d %b %Y, %I:%M %p')}`\n\n"
        "🚪 **Closing Reason:**\n"
        f"└ *{closing_reason}*\n\n"
        f"{summary_emoji} **Overall Summary:**\n"
        f"└ *{overall_summary}*\n\n"
        "📥 **Your Ticket Transcript:**\n"
        f"└ 📄 **File:** `{filename}` *({transcript_size:.2f} KB)*"
    )
    if TICKET_TRANSCRIPT_CHANNEL_ID:
        try:
            log_channel = await fetch_text_channel(TICKET_TRANSCRIPT_CHANNEL_ID)
            if log_channel is None:
                logger.warning("Transcript channel %s could not be found", TICKET_TRANSCRIPT_CHANNEL_ID)
            else:
                await log_channel.send(
                    view=create_ticket_archive_container(log_text, filename),
                    file=discord.File(io.BytesIO(file_data.getvalue()), filename=filename),
                )
        except discord.DiscordException:
            logger.exception("Could not send ticket archive to channel %s", TICKET_TRANSCRIPT_CHANNEL_ID)
    else:
        logger.warning("TICKET_TRANSCRIPT_CHANNEL_ID is not configured; ticket archive was not sent to a server channel")
    try:
        owner = await bot.fetch_user(ticket[1])
        dm_text = (
            "## 🧾 **HIGZEN STUDIO | TICKET CLOSED**\n"
            f"👋 Hello <@{ticket[1]}>, your ticket has been closed.\n\n"
            f"📁 **Ticket:** `{ticket_code}`\n"
            f"👤 **Opened By:** <@{ticket[1]}>\n\n"
            f"🚪 **Reason:** *{closing_reason}*\n"
            f"{summary_emoji} **Summary:** *{overall_summary}*\n\n"
            f"📥 **Transcript:** 📄 `{filename}`"
        )
        await owner.send(
            view=create_ticket_archive_container(dm_text, filename),
            file=discord.File(io.BytesIO(file_data.getvalue()), filename=filename),
        )
    except discord.DiscordException:
        logger.exception("Could not DM ticket transcript to user %s", ticket[1])


class GiveawayView(discord.ui.LayoutView):
    def __init__(self, giveaway_id: int, content: str, entry_count: int | None = None) -> None:
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.base_content = content
        entry_count = get_entry_count(giveaway_id) if entry_count is None else entry_count
        enter_button = discord.ui.Button(
            label="Enter Giveaway",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway:enter:{giveaway_id}",
        )
        enter_button.callback = self.enter_callback
        self.add_item(discord.ui.ActionRow(enter_button))

    async def enter_callback(self, interaction: discord.Interaction) -> None:
        ended = await asyncio.to_thread(giveaway_has_ended, self.giveaway_id)
        if ended:
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} This giveaway has ended, so new entries are closed."),
                ephemeral=True,
            )
            return

        entered = await asyncio.to_thread(user_has_giveaway_entry, self.giveaway_id, interaction.user.id)
        if entered:
            await asyncio.to_thread(remove_giveaway_entry, self.giveaway_id, interaction.user.id)
            entry_count = await asyncio.to_thread(get_entry_count, self.giveaway_id)
            if isinstance(interaction.message, discord.Message):
                updated_content = stylize_message(f"{self.base_content}\n**Entries:** {entry_count}")
                await interaction.message.edit(content=updated_content, view=GiveawayView(self.giveaway_id, self.base_content, entry_count))
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} You exited the giveaway."), ephemeral=True
            )
            return

        added = await asyncio.to_thread(add_giveaway_entry, self.giveaway_id, interaction.user.id)
        if added:
            entry_count = await asyncio.to_thread(get_entry_count, self.giveaway_id)
            if isinstance(interaction.message, discord.Message):
                updated_content = stylize_message(f"{self.base_content}\n**Entries:** {entry_count}")
                await interaction.message.edit(content=updated_content, view=GiveawayView(self.giveaway_id, self.base_content, entry_count))
            correct_emoji = interaction_emoji(interaction, "tickk", "✅")
            await interaction.response.send_message(
                bold_message(f"{correct_emoji} You entered the giveaway! Good luck!"), ephemeral=True
            )
        else:
            wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} You are already entered in this giveaway."), ephemeral=True
            )


class GiveawayModal(discord.ui.Modal, title="Create a Giveaway"):
    duration = discord.ui.TextInput(
        label="Duration *",
        placeholder="30 seconds, 2 hours, or 3 days",
        max_length=20,
    )
    winner_count = discord.ui.TextInput(
        label="Number of Winners *",
        placeholder="1",
        default="1",
        max_length=3,
    )
    prize = discord.ui.TextInput(
        label="Prize *",
        placeholder="What can people win?",
        max_length=200,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="Tell people about this giveaway...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )
    claim_time = discord.ui.TextInput(
        label="Claim time *",
        placeholder="30 seconds, 2 hours, or 3 days",
        default="1 hour",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.guild, discord.Guild) or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                bold_message("❌ Use this command in a server text channel."), ephemeral=True
            )
            return
        wrong_emoji = interaction_emoji(interaction, "xx_", "❌")

        duration_seconds = parse_duration(self.duration.value)
        claim_seconds = parse_duration(self.claim_time.value)
        try:
            winner_count = int(self.winner_count.value)
        except ValueError:
            winner_count = 0

        if duration_seconds is None:
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} Duration must look like `30 seconds`, `2 hours`, or `3 days`."), ephemeral=True
            )
            return
        if claim_seconds is None:
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} Claim time must look like `30 seconds`, `2 hours`, or `3 days`."), ephemeral=True
            )
            return
        if not 1 <= winner_count <= 100:
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} Number of winners must be between 1 and 100."), ephemeral=True
            )
            return

        end_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        giveaway_id = await asyncio.to_thread(
            create_giveaway,
            interaction.guild.id,
            interaction.channel.id,
            self.prize.value,
            self.description.value or "Good luck!",
            winner_count,
            interaction.user.id,
            end_at,
            claim_seconds,
        )
        content = build_giveaway_content(
            self.prize.value,
            self.description.value or "Good luck!",
            winner_count,
            interaction.user.id,
            end_at.isoformat(),
            claim_seconds,
        )
        message = await interaction.channel.send(
            content=stylize_message(f"{content}\n**Entries:** 0"),
            view=GiveawayView(giveaway_id, content),
        )
        await asyncio.to_thread(set_giveaway_message_id, giveaway_id, message.id)
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Giveaway created."), ephemeral=True)


class WelcomeView(discord.ui.LayoutView):
    def __init__(self, member: discord.Member) -> None:
        super().__init__(timeout=None)
        title_template, description_template, image_template = get_welcome_config(member.guild.id)
        replacements = {
            "{server}": member.guild.name,
            "{member}": member.mention,
            "{username}": member.display_name,
            "{avatar}": member.display_avatar.url,
        }
        title = title_template
        description = description_template
        image_url = image_template
        for placeholder, value in replacements.items():
            title = title.replace(placeholder, value)
            description = description.replace(placeholder, value)
            image_url = image_url.replace(placeholder, value)

        greet_button = discord.ui.Button(
            label="Greet",
            emoji="👋",
            style=discord.ButtonStyle.secondary,
            custom_id=f"welcome:greet:{member.id}",
        )
        greet_button.callback = self.greet
        welcome_items: list[discord.ui.Item] = [
            discord.ui.Section(
                discord.ui.TextDisplay(stylize_message(f"# {title}\n\n{description}")),
                accessory=discord.ui.Thumbnail(member.display_avatar.url),
            ),
            discord.ui.Separator(),
        ]
        if image_url and image_url != member.display_avatar.url:
            welcome_items.append(
                discord.ui.MediaGallery(discord.MediaGalleryItem(image_url))
            )
            welcome_items.append(discord.ui.Separator())
        welcome_items.append(discord.ui.ActionRow(greet_button))
        self.add_item(discord.ui.Container(*welcome_items))

    async def greet(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            bold_message(f"👋 Welcome to {interaction.guild.name}, {interaction.user.mention}!"),
            ephemeral=True,
        )


def create_welcome_embed(member: discord.Member) -> discord.Embed:
    title_template, description_template, image_template = get_welcome_config(member.guild.id)
    replacements = {
        "{server}": member.guild.name,
        "{member}": member.mention,
        "{username}": member.display_name,
        "{avatar}": member.display_avatar.url,
    }
    title = title_template
    description = description_template
    image_url = image_template
    for placeholder, value in replacements.items():
        title = title.replace(placeholder, value)
        description = description.replace(placeholder, value)
        image_url = image_url.replace(placeholder, value)
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if image_url:
        embed.set_image(url=image_url)
    return embed


async def send_welcome(member: discord.Member) -> None:
    if not WELCOME_CHANNEL_ID:
        logger.warning("WELCOME_CHANNEL_ID is not configured")
        return

    channel = member.guild.get_channel(int(WELCOME_CHANNEL_ID))
    if isinstance(channel, discord.TextChannel):
        await channel.send(view=WelcomeView(member))


@bot.tree.command(name="ping", description="Check whether the bot is responding")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency_ms}ms")


@bot.tree.command(name="hello", description="Say hello to the server")
async def hello(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Hello, {interaction.user.mention}!")


@bot.tree.command(name="order", description="Create a new pending order")
async def order(interaction: discord.Interaction) -> None:
    await interaction.response.send_modal(OrderModal())


def order_list_content(orders: list[tuple], title: str) -> str:
    if not orders:
        return f"## **{title}**\n\nNo orders found."
    lines = [f"## **{title}**", ""]
    for order_id, username, product_details, deadline, status, requester_id, claimed_by in orders[:25]:
        status_label = {
            "pending": "⏳ Pending",
            "claimed": "🛠️ Claimed",
            "cancelled": f"{server_emoji(bot.get_guild(order[1]), 'xx_', '❌')} Cancelled",
            "completed": f"{server_emoji(bot.get_guild(order[1]), 'tickk', '✅')} Completed",
        }.get(status, status.title())
        lines.append(
            f"**Order #{order_id}** | **{status_label}**\n"
            f"**Username:** {username}\n"
            f"**Product:** {product_details[:180]}\n"
            f"**Deadline:** {deadline}\n"
        )
    if len(orders) > 25:
        lines.append(f"\nShowing 25 of {len(orders)} orders.")
    return "\n".join(lines)[:4000]


@bot.tree.command(name="currentorders", description="View pending and claimed orders")
@app_commands.default_permissions(manage_guild=True)
async def currentorders(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    orders = await asyncio.to_thread(get_orders, interaction.guild.id, None)
    active_orders = [order for order in orders if order[4] in {"pending", "claimed"}]
    await interaction.response.send_message(view=create_message_container("current orders", order_list_content(active_orders, "Current Orders")), ephemeral=True)


@bot.tree.command(name="orders", description="View the complete order history")
@app_commands.choices(
    status=[
        app_commands.Choice(name="All orders", value="all"),
        app_commands.Choice(name="Pending", value="pending"),
        app_commands.Choice(name="Claimed", value="claimed"),
        app_commands.Choice(name="Cancelled", value="cancelled"),
        app_commands.Choice(name="Completed", value="completed"),
    ]
)
@app_commands.default_permissions(manage_guild=True)
async def orders(interaction: discord.Interaction, status: app_commands.Choice[str]) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    selected_status = None if status.value == "all" else status.value
    order_rows = await asyncio.to_thread(get_orders, interaction.guild.id, selected_status)
    await interaction.response.send_message(view=create_message_container("order list", order_list_content(order_rows, "Full Order List")), ephemeral=True)


def client_history_content(client_name: str, history: list[tuple]) -> str:
    if not history:
        return f"## **Client History**\n\nNo history found for **{client_name}**."
    lines = [f"## **Client History: {client_name}**", ""]
    for action, heading, message, buying_list, changed_by, changed_at in history[:10]:
        changed_time = datetime.fromisoformat(changed_at).astimezone(IST).strftime("%d %B %Y %I:%M %p IST")
        lines.append(
            f"**{action.title()}** | **{changed_time}** | **By:** <@{changed_by}>\n"
            f"**Heading:** {heading}\n"
            f"**Message:** {message[:300]}\n"
            f"**Buying List:** {buying_list[:300]}\n"
        )
    if len(history) > 10:
        lines.append(f"Showing the latest 10 of {len(history)} changes.")
    return "\n".join(lines)[:4000]


@bot.tree.command(name="client", description="Create, view, edit, or view history for a client")
@app_commands.describe(
    action="What you want to do with the client record",
    client_name="The exact name or part of the client name",
    heading="Profile heading, required for create and edit",
    message="Customer notes or profile message, required for create and edit",
    buying_list="Products the customer bought or wants to buy",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Create profile", value="create"),
        app_commands.Choice(name="View profile", value="view"),
        app_commands.Choice(name="Edit profile", value="edit"),
        app_commands.Choice(name="View history", value="history"),
    ]
)
@app_commands.default_permissions(manage_guild=True)
async def client(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    client_name: str,
    heading: str | None = None,
    message: str | None = None,
    buying_list: str | None = None,
) -> None:
    wrong_emoji = interaction_emoji(interaction, "xx_", "❌")
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            bold_message(f"{wrong_emoji} This command can only be used in a server."), ephemeral=True
        )
        return

    client_name = client_name.strip()
    heading = heading.strip() if heading else None
    message = message.strip() if message else None
    buying_list = buying_list.strip() if buying_list else None
    if action.value in {"create", "edit"} and (not heading or not message or not buying_list):
        await interaction.response.send_message(
            bold_message(
                f"{wrong_emoji} Create and edit require heading, message, and buying list."
            ),
            ephemeral=True,
        )
        return

    if action.value == "create":
        client_id = await asyncio.to_thread(
            create_client,
            interaction.guild.id,
            client_name.strip(),
            heading.strip(),
            message.strip(),
            buying_list.strip(),
            interaction.user.id,
        )
        if client_id is None:
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} A client with that name already exists."), ephemeral=True
            )
            return
        profile = await asyncio.to_thread(find_client, interaction.guild.id, client_name)
        await interaction.response.send_message(
            view=create_message_container("client profile created", client_content(profile)), ephemeral=True
        )
        return

    if action.value == "edit":
        profile = await asyncio.to_thread(
            update_client,
            interaction.guild.id,
            client_name.strip(),
            heading.strip(),
            message.strip(),
            buying_list.strip(),
            interaction.user.id,
        )
        if profile is None:
            await interaction.response.send_message(
                bold_message(f"{wrong_emoji} No client profile matched that name."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            view=create_message_container("client profile updated", client_content(profile)), ephemeral=True
        )
        return

    if action.value == "history":
        history = await asyncio.to_thread(get_client_history, interaction.guild.id, client_name.strip())
        await interaction.response.send_message(
            view=create_message_container("client history", client_history_content(client_name, history)), ephemeral=True
        )
        return

    profile = await asyncio.to_thread(find_client, interaction.guild.id, client_name.strip())
    if profile is None:
        await interaction.response.send_message(
            bold_message(f"{wrong_emoji} No client profile matched that name."), ephemeral=True
        )
        return
    await interaction.response.send_message(
        view=create_message_container("client profile", client_content(profile)), ephemeral=True
    )


@bot.tree.command(name="giveaway", description="Cʀᴇαᴛᴇ α gɪᴠᴇαωαʏ")
@app_commands.default_permissions(manage_guild=True)
async def giveaway(interaction: discord.Interaction) -> None:
    await interaction.response.send_modal(GiveawayModal())


@bot.tree.command(name="ticketpanel", description="Create or update the Higzen Studio ticket panel")
@app_commands.describe(
    channel="Channel where the panel should be posted",
    title="Panel heading",
    description="Panel welcome and support text",
    footer="Panel footer text",
    message_id="Existing panel message ID to edit instead of posting a new one",
    category1_name="Category 1 heading",
    category1_description="Category 1 description",
    category1_emoji="Category 1 emoji",
    category2_name="Category 2 heading",
    category2_description="Category 2 description",
    category2_emoji="Category 2 emoji",
    category3_name="Category 3 heading",
    category3_description="Category 3 description",
    category3_emoji="Category 3 emoji",
    category4_name="Category 4 heading",
    category4_description="Category 4 description",
    category4_emoji="Category 4 emoji",
    category5_name="Category 5 heading",
    category5_description="Category 5 description",
    category5_emoji="Category 5 emoji",
)
@app_commands.default_permissions(manage_guild=True)
async def ticketpanel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str | None = None,
    description: str | None = None,
    footer: str | None = None,
    message_id: str | None = None,
    category1_name: str | None = None,
    category1_description: str | None = None,
    category1_emoji: str | None = None,
    category2_name: str | None = None,
    category2_description: str | None = None,
    category2_emoji: str | None = None,
    category3_name: str | None = None,
    category3_description: str | None = None,
    category3_emoji: str | None = None,
    category4_name: str | None = None,
    category4_description: str | None = None,
    category4_emoji: str | None = None,
    category5_name: str | None = None,
    category5_description: str | None = None,
    category5_emoji: str | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    current_config = await asyncio.to_thread(get_ticket_config, interaction.guild.id)
    category_values = [
        (category1_name, category1_description, category1_emoji),
        (category2_name, category2_description, category2_emoji),
        (category3_name, category3_description, category3_emoji),
        (category4_name, category4_description, category4_emoji),
        (category5_name, category5_description, category5_emoji),
    ]
    if not any(value for category in category_values for value in category):
        category_list = current_config[2]
    else:
        category_list = []
        for category_index, (category_name, category_description, category_emoji) in enumerate(category_values, start=1):
            category_parts = [category_name, category_description, category_emoji]
            if any(category_parts) and not all(category_parts):
                await interaction.response.send_message(
                    f"Category {category_index} needs a heading, description, and emoji.", ephemeral=True
                )
                return
            if all(category_parts):
                if len(category_name.strip()) > 100 or len(category_description.strip()) > 100:
                    await interaction.response.send_message(
                        f"Category {category_index} heading and description must be 100 characters or fewer.",
                        ephemeral=True,
                    )
                    return
                category_list.append(
                    {
                        "name": category_name.strip(),
                        "description": category_description.strip(),
                        "emoji": category_emoji.strip(),
                    }
                )

        if not 1 <= len(category_list) <= 25:
            await interaction.response.send_message("Add at least one complete category.", ephemeral=True)
            return

    title = title or current_config[0]
    description = description or current_config[1]
    footer = footer or current_config[3]
    try:
        existing_message_id = int(message_id) if message_id else current_config[5]
    except ValueError:
        await interaction.response.send_message("message_id must be a Discord message ID.", ephemeral=True)
        return
    await asyncio.to_thread(
        save_ticket_config,
        interaction.guild.id,
        title,
        description,
        category_list,
        footer,
        channel.id,
        existing_message_id,
    )
    if existing_message_id and await refresh_ticket_panel(interaction.guild.id):
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Ticket panel updated in {channel.mention}."), ephemeral=True)
        return
    panel_message = await channel.send(view=TicketPanelView(interaction.guild.id))
    await asyncio.to_thread(save_ticket_config, interaction.guild.id, title, description, category_list, footer, channel.id, panel_message.id)
    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(bold_message(f"{correct_emoji} Ticket panel posted in {channel.mention}."), ephemeral=True)


@bot.tree.command(name="ticketpaneledit", description="Edit the existing ticket panel text")
@app_commands.describe(title="New panel title", description="New panel description", footer="New panel footer")
@app_commands.default_permissions(manage_guild=True)
async def ticketpaneledit(
    interaction: discord.Interaction,
    title: str | None = None,
    description: str | None = None,
    footer: str | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild) or not title and not description and not footer:
        await interaction.response.send_message("Provide a new title, description, or footer.", ephemeral=True)
        return
    old_title, old_description, categories, old_footer, panel_channel_id, panel_message_id = await asyncio.to_thread(
        get_ticket_config, interaction.guild.id
    )
    await asyncio.to_thread(
        save_ticket_config,
        interaction.guild.id,
        title or old_title,
        description or old_description,
        categories,
        footer or old_footer,
        panel_channel_id,
        panel_message_id,
    )
    if not await refresh_ticket_panel(interaction.guild.id):
        await interaction.response.send_message("Panel text saved, but no existing panel message was found.", ephemeral=True)
        return
    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(bold_message(f"{correct_emoji} Ticket panel text updated."), ephemeral=True)


@bot.tree.command(name="ticketcategory", description="Add or remove a ticket category")
@app_commands.describe(
    action="Add or remove a category",
    panel_channel="The saved ticket panel to edit",
    category_heading="Category heading",
    category_description="Category description, required when adding",
    emoji="Unicode or custom emoji, required when adding",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Add category", value="add"),
        app_commands.Choice(name="Remove category", value="remove"),
    ]
)
@app_commands.default_permissions(manage_guild=True)
async def ticketcategory(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    panel_channel: discord.TextChannel,
    category_heading: str,
    category_description: str | None = None,
    emoji: str | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    category_heading = category_heading.strip()
    if not category_heading or len(category_heading) > 100:
        await interaction.response.send_message("Category names must be between 1 and 100 characters.", ephemeral=True)
        return
    title, description, categories, footer, panel_channel_id, panel_message_id = await asyncio.to_thread(
        get_ticket_config, interaction.guild.id
    )
    if panel_channel_id != panel_channel.id:
        await interaction.response.send_message("That channel is not the saved ticket panel.", ephemeral=True)
        return
    if action.value == "add":
        category_description = category_description.strip() if category_description else None
        emoji = emoji.strip() if emoji else None
        if not category_description or not emoji:
            await interaction.response.send_message("Adding requires a description and emoji.", ephemeral=True)
            return
        if len(category_description) > 100:
            await interaction.response.send_message("Category descriptions must be 100 characters or fewer.", ephemeral=True)
            return
        if any(category["name"].casefold() == category_heading.casefold() for category in categories):
            await interaction.response.send_message("That ticket category already exists.", ephemeral=True)
            return
        if len(categories) >= 25:
            await interaction.response.send_message("Discord allows a maximum of 25 categories in one menu.", ephemeral=True)
            return
        categories.append({"name": category_heading, "description": category_description, "emoji": emoji})
    else:
        updated_categories = [
            category for category in categories if category["name"].casefold() != category_heading.casefold()
        ]
        if len(updated_categories) == len(categories):
            await interaction.response.send_message("That ticket category was not found.", ephemeral=True)
            return
        if not updated_categories:
            await interaction.response.send_message("Keep at least one ticket category.", ephemeral=True)
            return
        categories = updated_categories

    await asyncio.to_thread(
        save_ticket_config,
        interaction.guild.id,
        title,
        description,
        categories,
        footer,
        panel_channel_id,
        panel_message_id,
    )
    if await refresh_ticket_panel(interaction.guild.id):
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Ticket categories updated in the existing panel."), ephemeral=True)
    else:
        correct_emoji = interaction_emoji(interaction, "tickk", "✅")
        await interaction.response.send_message(bold_message(f"{correct_emoji} Category saved. Run `/ticketpanel` to publish the panel."), ephemeral=True)


@bot.tree.command(name="ticketcategoryfreeze", description="Temporarily stop new tickets for a panel category")
@app_commands.describe(
    category="The category to freeze",
    panel_channel="Optional panel channel; must match the saved panel",
)
@app_commands.default_permissions(manage_guild=True)
async def ticketcategoryfreeze(
    interaction: discord.Interaction,
    category: str,
    panel_channel: discord.TextChannel | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    title, description, categories, footer, saved_channel_id, panel_message_id = await asyncio.to_thread(
        get_ticket_config, interaction.guild.id
    )
    if panel_channel and saved_channel_id != panel_channel.id:
        await interaction.response.send_message("That channel is not the saved ticket panel channel.", ephemeral=True)
        return
    selected = next((item for item in categories if item["name"].casefold() == category.strip().casefold()), None)
    if selected is None:
        await interaction.response.send_message("That ticket category was not found in the saved panel.", ephemeral=True)
        return
    if selected.get("frozen_at"):
        await interaction.response.send_message("That ticket category is already frozen.", ephemeral=True)
        return

    frozen_at = datetime.now(timezone.utc)
    deadline = frozen_at + timedelta(days=3)
    selected["frozen_at"] = frozen_at.isoformat()
    selected["freeze_deadline"] = deadline.isoformat()
    await asyncio.to_thread(
        save_ticket_config,
        interaction.guild.id,
        title,
        description,
        categories,
        footer,
        saved_channel_id,
        panel_message_id,
    )
    await refresh_ticket_panel(interaction.guild.id)
    await send_category_announcement(
        interaction.guild,
        "## <:aa:1548999776111173702>** Tɪᴄᴋᴇᴛ Cαᴛᴇɢᴏʀʏ Oᴛʜᴇʀ Fʀᴏᴢᴇɴ Cαᴛᴇɢᴏʀʏ: Oᴛʜᴇʀ**\n"
        "-    **Rᴇαsᴏɴ: Cʀɪᴛɪᴄαʟ ʜɪɢʜ ᴛɪᴄᴋᴇᴛ ᴛʀαꜛꜛɪᴄ**\n"
        "-    **Rᴇsᴄᴍᴘᴛɪᴏɴ Cᴏɴᴅɪᴛɪᴏɴ: Wɪʟʟ ʙᴇ ʀᴇᴍᴏᴠᴇᴅ αs sᴏᴏɴ αs ᴛʀαꜛꜛɪᴄ ʙᴇᴄᴏᴍᴇs ʟᴏω **\n"
        f"-    **Esᴛɪᴍαᴛᴇᴅ Rᴇʟᴇαsᴇ: {deadline.astimezone(IST).strftime('%d %B %Y, %I:%M %p IST')}**<:aa:1548999776111173702>",
    )
    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(bold_message(f"{correct_emoji} `{selected['name']}` has been frozen for new tickets."), ephemeral=True)


@bot.tree.command(name="ticketcategoryunfreeze", description="Restart new tickets for a frozen panel category")
@app_commands.describe(
    category="The category to unfreeze",
    panel_channel="Optional panel channel; must match the saved panel",
)
@app_commands.default_permissions(manage_guild=True)
async def ticketcategoryunfreeze(
    interaction: discord.Interaction,
    category: str,
    panel_channel: discord.TextChannel | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    title, description, categories, footer, saved_channel_id, panel_message_id = await asyncio.to_thread(
        get_ticket_config, interaction.guild.id
    )
    if panel_channel and saved_channel_id != panel_channel.id:
        await interaction.response.send_message("That channel is not the saved ticket panel channel.", ephemeral=True)
        return
    selected = next((item for item in categories if item["name"].casefold() == category.strip().casefold()), None)
    if selected is None:
        await interaction.response.send_message("That ticket category was not found in the saved panel.", ephemeral=True)
        return
    if not selected.get("frozen_at"):
        await interaction.response.send_message("That ticket category is not frozen.", ephemeral=True)
        return

    unfrozen_at = datetime.now(timezone.utc)
    selected.pop("frozen_at", None)
    selected.pop("freeze_deadline", None)
    await asyncio.to_thread(
        save_ticket_config,
        interaction.guild.id,
        title,
        description,
        categories,
        footer,
        saved_channel_id,
        panel_message_id,
    )
    await refresh_ticket_panel(interaction.guild.id)
    await send_category_announcement(
        interaction.guild,
        "## <:aa:1548999776111173702>** Tɪᴄᴋᴇᴛ Cαᴛᴇɢᴏʀʏ Oᴛʜᴇʀ Uɴꜛʀᴏᴢᴇɴ Cαᴛᴇɢᴏʀʏ: Oᴛʜᴇʀ**\n"
        "-    **Rᴇαsᴏɴ: Tɪᴄᴋᴇᴛ ᴛʀαꜛꜛɪᴄ ʜαs ʀᴇᴛᴄʀɴᴇᴅ ᴛᴏ α ᴍαɴαɢᴇαʙʟᴇ ʟᴇᴠᴇʟ**\n"
        "-    **Rᴇsᴄᴍᴘᴛɪᴏɴ Cᴏɴᴅɪᴛɪᴏɴ: Sᴛαɴᴅαʀᴅ ᴏᴘᴇʀαᴛɪᴏɴs ʜαᴠᴇ ʀᴇsᴄᴍᴇᴅ**\n"
        f"-    **Rᴇʟᴇαsᴇ: Cᴏᴍᴘʟᴇᴛᴇᴅ ᴏɴ {unfrozen_at.astimezone(IST).strftime('%d %B %Y, %I:%M %p IST')}**<:aa:1548999776111173702>",
    )
    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(bold_message(f"{correct_emoji} `{selected['name']}` has been unfrozen."), ephemeral=True)


@bot.tree.command(name="ticketadd", description="Add a member to the current ticket")
@app_commands.describe(member="Member who should be added to this ticket")
async def ticketadd(interaction: discord.Interaction, member: discord.Member) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("This command can only be used inside a ticket.", ephemeral=True)
        return
    if get_ticket(interaction.channel.id) is None:
        await interaction.response.send_message("This channel is not a ticket.", ephemeral=True)
        return
    if not await TicketView(interaction.channel.id).require_mod(interaction):
        return
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(bold_message(f"{correct_emoji} Added {member.mention} to this ticket."))


@bot.tree.command(name="sendmessage", description="Sᴇɴᴅ α ᴍᴇssαɢᴇ ɴᴏω ᴏʀ ᴄʜᴇᴅᴜʟᴇ ɪᴛ")
@app_commands.describe(
    channel="The channel that should receive the message",
    message_type="The message type shown at the top of the container",
    heading="Optional custom heading, especially useful with Other",
    content="The message text",
    send_date="Optional IST date in YYYY-MM-DD format",
    send_time="Optional IST time in HH:MM format",
)
@app_commands.choices(
    message_type=[
        app_commands.Choice(name="Announcement", value="announcement"),
        app_commands.Choice(name="Rules", value="rules"),
        app_commands.Choice(name="Information", value="information"),
        app_commands.Choice(name="Reminder", value="reminder"),
        app_commands.Choice(name="Other", value="other"),
    ]
)
@app_commands.default_permissions(manage_guild=True)
async def sendmessage(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message_type: app_commands.Choice[str],
    content: str,
    heading: str | None = None,
    send_date: str | None = None,
    send_time: str | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    if len(content) > 4000:
        await interaction.response.send_message("The message must be 4000 characters or fewer.", ephemeral=True)
        return

    heading = (heading or "").strip()
    if len(heading) > 200:
        await interaction.response.send_message("The heading must be 200 characters or fewer.", ephemeral=True)
        return

    if bool(send_date) != bool(send_time):
        await interaction.response.send_message(
            "Enter both send date and send time, or leave both empty for immediate sending.",
            ephemeral=True,
        )
        return

    if send_date and send_time:
        try:
            scheduled_at = datetime.strptime(
                f"{send_date} {send_time}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=IST).astimezone(timezone.utc)
        except ValueError:
            await interaction.response.send_message(
                "Use IST date `YYYY-MM-DD` and time `HH:MM`, for example `2026-09-05` and `18:30`.",
                ephemeral=True,
            )
            return

        if scheduled_at <= datetime.now(timezone.utc):
            await interaction.response.send_message("The scheduled time must be in the future.", ephemeral=True)
            return

        await asyncio.to_thread(
            save_scheduled_message,
            interaction.guild.id,
            channel.id,
            message_type.value,
            content,
            heading,
            scheduled_at,
        )
        local_time = scheduled_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        await interaction.response.send_message(
            f"Scheduled the {message_type.name.lower()} message for {channel.mention} at **{local_time}**.",
            ephemeral=True,
        )
        return

    await send_container_message(channel, message_type.value, content, heading)
    await interaction.response.send_message(f"Sent the message to {channel.mention}.", ephemeral=True)


class HelpView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=180)
        select = discord.ui.Select(
            placeholder="Choose a command group...",
            options=[
                discord.SelectOption(label="General", value="general", description="Basic bot commands"),
                discord.SelectOption(label="Tickets", value="tickets", description="Ticket setup and staff controls"),
                discord.SelectOption(label="Orders", value="orders", description="Order workflow commands"),
                discord.SelectOption(label="Admin Tools", value="admin", description="Messages, giveaways, and welcome settings"),
            ],
        )
        select.callback = self.select_callback
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(stylize_message("### 🗺️ MAIN COMMAND DIRECTORY 🗺️")),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    stylize_message("### 📢 EXPLORE AVAILABLE COMMANDS\n\n"
                    "Select a command group from the selection menu below to unlock the full list of features, detailed usage instructions, and system descriptions.")
                ),
                discord.ui.ActionRow(select),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    stylize_message("### 💡 QUICK TIPS\n"
                    "* Click the **dropdown menu** below to filter by category.\n"
                    "* Each group contains specific permissions and parameters.")
                ),
            )
        )

    async def select_callback(self, interaction: discord.Interaction) -> None:
        group = str(interaction.data.get("values", ["general"])[0]) if interaction.data else "general"
        content = {
            "general": "**/help** - Open this command guide.\n**/ping** - Check bot latency.\n**/hello** - Send a greeting.",
            "tickets": "**/ticketpanel** - Create or update a ticket panel with up to five separate category boxes.\n**/ticketpaneledit** - Edit title, description, or footer.\n**/ticketcategory** - Choose a panel, then add or remove a category with separate heading, emoji, and description fields.\n**/ticketcategoryfreeze** - Stop a category for high traffic.\n**/ticketcategoryunfreeze** - Restart a frozen category.\n**/ticketadd** - Add a member to the current ticket.\nTicket staff buttons claim/release, hold/unhold, or close tickets.",
            "orders": "**/order** - Submit a new order.\n**/currentorders** - View pending and claimed orders.\n**/orders** - View order history.\nStaff can claim, cancel, or complete orders from the order buttons.",
            "admin": "**/sendmessage** - Send or schedule a container message.\n**/giveaway** - Create a giveaway.\n**/client** - Manage client profiles and history.\n**/welcomeedit** - Edit welcome text and image.\n**/testwelcome** - Preview the welcome message.",
        }[group]
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(view=create_message_container("help", content), ephemeral=True)


@bot.tree.command(name="help", description="Show all bot commands and their uses")
async def help_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        view=HelpView(),
        ephemeral=True,
    )


@bot.tree.command(name="setconfig", description="Uᴘᴅαᴛᴇ ʙᴏᴛ cᴏɴꜰɪɢᴜʀαᴛɪᴏɴ sᴇᴛᴛɪɴɢs")
@app_commands.describe(
    name="Setting name, for example WELCOME_CHANNEL_ID or ANNOUNCEMENT_CHANNEL_ID",
    value="New value; leave blank to clear the setting",
)
@app_commands.default_permissions(manage_guild=True)
async def setconfig(
    interaction: discord.Interaction,
    name: str,
    value: str | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    normalized_name = name.strip().upper()
    if normalized_name not in ENV_SETTING_KEYS:
        valid_options = ", ".join(ENV_SETTING_KEYS)
        await interaction.response.send_message(
            bold_message(f"❌ Invalid setting name. Available settings: {valid_options}"),
            ephemeral=True,
        )
        return

    normalized_value = normalize_setting_value(value)
    try:
        update_env_setting(normalized_name, normalized_value)
    except OSError:
        await interaction.response.send_message(
            bold_message(f"❌ Could not update {normalized_name}. Check the bot file permissions."),
            ephemeral=True,
        )
        return

    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(
        bold_message(f"{correct_emoji} `{normalized_name}` was updated successfully."),
        ephemeral=True,
    )


@bot.tree.command(name="welcomeedit", description="Eᴅɪᴛ ᴛʜᴇ ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssαɢᴇ αɴᴅ ɪᴍαɢᴇ")
@app_commands.describe(
    title="Welcome title; supports {server}, {member}, and {username}",
    description="Welcome text; supports {server}, {member}, {username}, and {avatar}",
    image_url="Large image URL, or {avatar} to use the member avatar",
)
@app_commands.default_permissions(manage_guild=True)
async def welcomeedit(
    interaction: discord.Interaction,
    title: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    old_title, old_description, old_image_url = await asyncio.to_thread(get_welcome_config, interaction.guild.id)
    if title is None and description is None and image_url is None:
        await interaction.response.send_message("Provide a new title, description, or image URL.", ephemeral=True)
        return
    await asyncio.to_thread(
        save_welcome_config,
        interaction.guild.id,
        title or old_title,
        description or old_description,
        image_url or old_image_url,
    )
    correct_emoji = interaction_emoji(interaction, "tickk", "✅")
    await interaction.response.send_message(
        bold_message(f"{correct_emoji} Welcome message settings updated. Use `/testwelcome` to preview them."),
        ephemeral=True,
    )


@bot.tree.command(name="testwelcome", description="Pʀᴇᴠɪᴇω ᴛʜᴇ sᴇʀᴠᴇʀ ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssαɢᴇ")
@app_commands.default_permissions(manage_guild=True)
async def testwelcome(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.send_message(
        view=WelcomeView(interaction.user),
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to a .env file.")
    bot.run(TOKEN)