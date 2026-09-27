# Discord Bot

A small Discord bot built with Python and `discord.py`.

## Setup

1. Install Python 3.10 or newer from https://www.python.org/downloads/.
2. In the project folder, create a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and add your bot token and application ID. The application ID is your Discord Developer Portal **Application ID**; it is used for the bot invite link and is not secret.
5. Set `WELCOME_CHANNEL_ID` to the channel where welcome messages should be posted, set `WELCOME_IMAGE_URL` to the large welcome banner image URL, and set `AUTO_ROLE_ID` to the role new members should receive.
6. In the Discord Developer Portal, create an application, add a bot, and invite it with the `bot` and `applications.commands` scopes. Enable **Server Members Intent** under Bot settings.
7. Start the bot:

   ```powershell
   python main.py
   ```

   `python bot.py` also remains supported.

The bot provides `/ping`, `/hello`, `/help`, `/testwelcome`, an admin-only `/sendmessage` command, and an admin-only `/giveaway` command. The message command lets you select a target channel, choose a type including **Other**, leave the heading empty or enter a custom heading, enter text, and optionally schedule it using IST date and time. The giveaway command opens a form for duration, winner count, prize, description, and claim time, then posts a Components V2 container with an entry button and live entry count. When it ends, it sends a normal congratulatory winner message with the claim time. Scheduled messages and giveaways are stored in SQLite and survive restarts. It assigns `AUTO_ROLE_ID` and posts the welcome embed automatically when someone joins. Set `DISCORD_GUILD_ID` while developing so commands appear in that server quickly; omit it to sync commands globally.

The bot's role must be positioned above the automatic role in **Server Settings > Roles**, and the bot needs the **Manage Roles** permission.

The message command uses Discord Components V2, so install the current dependencies with `python -m pip install -r requirements.txt`. The bot needs **Send Messages** and **Use External Apps** permissions in the selected channel.

## Ticket system

Set `TICKET_MOD_ROLE_ID` to the moderator role, `TICKET_CATEGORY_ID` to the Discord category where private tickets should be created, and `TICKET_TRANSCRIPT_CHANNEL_ID` to the channel that should receive closed-ticket HTML archives. When staff close a ticket, they enter a closing reason and overall summary; the bot posts the ticket log and HTML file in the transcript channel and sends the same archive to the ticket owner by DM. Enable **Message Content Intent** in the Developer Portal for transcripts.

Set `ANNOUNCEMENT_CHANNEL_ID` to the channel that should receive ticket category status announcements. Staff can use `/ticketcategoryfreeze category:<name>` to stop a category in the saved ticket panel. New clicks receive a high-traffic unavailable message, and the announcement includes a fixed three-day estimated deadline. Use `/ticketcategoryunfreeze category:<name>` to restart it; the announcement includes how long the category was frozen.

Use `/ticketpanel` to post the Higzen Studio panel. Running `/ticketpanel` again for the same configured channel updates the existing panel message. The command provides five optional category groups. Each group has separate `category name`, `category description`, and `category emoji` fields; complete any number of groups from 1 to 5.

```text
category1_name: Support
category1_description: Get help from our support team.
category1_emoji: 🎫
category2_name: Report a Bug
category2_description: Report a server or bot issue.
category2_emoji: 🐛
```

The emoji field accepts normal Unicode emoji or custom Discord emoji such as `<:support:123456789012345678>`. Use these commands to edit the existing panel:

```text
/ticketpaneledit title:New Title description:New support instructions
/ticketcategory action:Add panel_channel:#tickets category_heading:Billing category_description:Payment help emoji:💳
/ticketcategory action:Remove panel_channel:#tickets category_heading:Other
```

`/ticketpaneledit` also accepts `footer`. To adopt and edit a panel message that was posted outside the bot, run `/ticketpanel` with its channel and `message_id`.

Ticket controls are persistent: **Put on Hold** changes to **Take Off Hold**, and **Claim Ticket** changes to **Release Claim**. The panel refreshes after either action and state survives a restart.

Use `/help` for the interactive command guide. Use `/welcomeedit` to edit the welcome title, text, and large image URL. Supported placeholders are `{server}`, `{member}`, `{username}`, and `{avatar}`; `/testwelcome` previews the result in the same large container used for new members. Giveaway duration and claim time accept seconds, minutes, hours, and days.

Staff can use `/ticketadd` inside a ticket, and only moderators with `Manage Channels` or the configured moderator role can close tickets.

### Web dashboard

Set `DASHBOARD_PASSWORD` in `.env`, restart the bot, and open `http://127.0.0.1:8080`. The dashboard lets you edit the Higzen Studio panel title, description, footer, categories, emojis, ticket button labels, channel IDs, moderator role, ticket channel prefix, welcome settings, order destinations, and server-change log destination. Its Tools tab can send or schedule messages, create giveaways and orders, manage client profiles and history, and send a welcome preview. Use **Save changes** to store settings in SQLite and **Publish to Discord** to update or create the panel. Keep the dashboard bound to `127.0.0.1` unless you are placing it behind a secure reverse proxy.

## Order system

Set `ORDER_PENDING_CHANNEL_ID` to the public pending-orders channel, `ORDER_STORAGE_CHANNEL_ID` to the private moderator storage channel, and `ORDER_COMPLETED_CHANNEL_ID` to the completed-orders channel. Set `ORDER_MOD_ROLE_ID` to the role allowed to claim, cancel, and complete orders; it falls back to `TICKET_MOD_ROLE_ID` when omitted.

Use `/order` to enter the username, product details, and deadline time. The bot posts a Components V2 container in pending orders and a mirrored moderator container with **Claim**, **Cancel**, and **Completed** buttons. Cancellation requires a reason. Completion removes the pending and storage copies and posts the completed order with the work-done IST timestamp. `/currentorders` shows ongoing work and `/orders` shows the complete stored history, including cancelled orders.

## Client database

Use the moderator-only `/client` command with one of these actions:

```text
/client action:create client_name:Alex heading:VIP Customer message:Prefers fast delivery buying_list:Hosting, Design
/client action:view client_name:Alex
/client action:edit client_name:Alex heading:Returning Customer message:Follow up next week buying_list:Hosting, Design, Support
/client action:history client_name:Alex
```

Client profiles and every create/edit snapshot are stored in the local SQLite database. Search accepts an exact name or part of a name. View and history responses are ephemeral, so only the moderator running the command can see customer data.

## Server change log

Set `SERVER_LOG_CHANNEL_ID` to the channel where live audit messages should be posted. The bot sends each detected change as a new Components V2 container and does not save these events to SQLite or files. It covers channel, role, server-setting, member, join/leave, ban/unban, thread, webhook, emoji, sticker, and message edit/delete changes. Give the bot **View Audit Log**, **View Channel**, and **Send Messages** permissions in that channel; enable **Message Content Intent** for message edit/delete details.

Never commit `.env` or share your bot token.