# lBIS Discord Bot

The lBIS Discord Bot is a reference implementation of the [lBIS API](https://github.com/inflatebot/lbis), demonstrating a fully-featured asynchronous remote play experience around its very minimal features.

By keeping the implementation of features and tracking of state on the frontend side, the API can be kept extremely lean, capable of running on any Wi-Fi-enabled microcontroller.

The bot is designed to be run on the wearer's PC, with the IP address of lBIS being provided in `bot.json`.

## Features

- Enables the wearer's play partners to inflate the wearer remotely for brief periods, with minimal effort on their part
- Enforces a maximum "session" length, with the ability to add time to a session or restart it remotely
- Has a "time bank" which can hold time requested by users that failed due to session constraints, latching in the middle of a timer, or technical issues, to a configurable maximum
- Identifies the wearer with a password provided by a command over DMs to enable locking the pump, arbitrary pump control, and session management.
- Displays the pump state (on/off/locked/unreachable), along with session/bank time, in the bot's status

## TODO

- Enable the wearer to constrain the pump controls to specific users
- Enable the wearer to designate users who can control the pump arbitrarily
- Limit the amount of time an individual user can have banked
- Provide a webhook to dynamically show the pump's state

## Installation

The easiest way to use lbis-discord is to use [`uv`](https://docs.astral.sh/uv/). I've interacted with many Python package management systems and `uv` blows them all out of the water.
- `git clone https://github.com/inflatebot/lbis_discord`
- `cd lbis_discord`
- `uv run bot.py`
If you insist on using a venv by yourself, `discord` should be the only package you need to install.

When being run for the first time, the bot will generate a `bot.json` file. Open this file, and set:
    - `discord_token` to a valid bot token, see [Bot Setup](#bot-setup) below
    - `api_base_url` to the URL of your lBIS device, in the following format: `http://[IP]:[PORT]`,
    - `wearer_secret` to an arbitrary value (that you should store securely).

## Bot Setup

1. In a browser, go to the [Discord Developer Portal,](https://discord.com/developers/applications) and create a new Application. Name it whatever you want.
2. (Optional) Under "Bot", give it an icon and banner.
3. Click "Reset Token", then click "Yes, do it!", enter your password, then copy the code into `"discord_token"` in `bot.json`.
4. (Optional) Enable Presence Intent, Server Members Intent, and Message Content Intent. These may become required in the future but aren't right now; currently Message Content is used to set a prefix, but the intended usage is with slash commands, so this may be removed. Since this bot is meant to be used with a closed circle of friends, you should never have to worry about verification.
5. Under "Installation", click "Scopes" under "Guild Install", and click "Bot". Copy the Discord-provided install link, and save it somewhere.
6. Open that link in a new tab and add it to a server.
7. Set `wearer_secret` in `bot.json` to something; it can be whatever you want, as long as you store it safely. Restart the bot.
7. DM the bot with "/set_wearer" alongside the secret you set. The bot should now DM you about its connection to lBIS, as well as tell you whenever someone uses a command. You will also be able to arbitrarily control the pump, reset and alter the session/bank, and set a latch on the pump to keep it from being activated.

## Command Reference

### Pump Control
- `/pump_on` - Wearer only. Does what you think it does.
- `/pump_off` - Wearer only. Does what you think it does.
- `/pump_timed` - Free usage. Turns on the pump for a provided number of seconds, up to a maximum of 60 (by default.)
- `/pump_banked` - Wearer only. Similar to `/pump_timed`, but uses "banked time", see below.
- `/latch` - Wearer only. Prevents the pump from being activated by any means under the bot's control. Can either be latched for a short period, or indefinitely. Can optionally set a reason for latching, which is displayed in the bot status.

### Session Control
All session control commands are wearer-only.
- `/set_time` - Set the maximum time the pump can run without intervention, **in minutes.** You probably don't want to set this very high.
- `/add_time` - Adds time to the session, **in minutes.**
- `/reset_time` - Sets the session back to the default in `bot.json` (or what was last set with `/set_time`.)

### Bank
- `/bank_time` - Wearer only. Manually add time to the bank.
- `/reset_bank` - Default on your debt <sup><sub>, coward. /j</sup></sub>
