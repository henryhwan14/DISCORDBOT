# Discord Economy Bot

This project implements a Discord bot that manages a virtual economy for your
server. The bot was refactored to remove Roblox integration while preserving
and polishing the original economy features such as account creation, transfers,
public accounts, administrator tooling, and scheduled tasks.

## Features

- Slash commands for users to create accounts, check balances, transfer funds,
  and review their latest transactions.
- Administrator-only commands for freezing accounts, minting currency, managing
  transaction fees, configuring public accounts, and exporting transaction
  history.
- Automated background jobs that periodically collect taxes and pay monthly
  salaries from a configured public account.
- Data persistence via JSON files stored alongside the bot.

## Getting Started

1. Create a Discord bot in the [Discord Developer Portal](https://discord.com/developers/applications)
   and invite it to your server with the necessary permissions.
2. Copy the bot token and create a `.env` file in the project root:

   ```env
   DISCORD_TOKEN=your_discord_bot_token
   ```

3. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Run the bot:

   ```bash
   python bot.py
   ```

The bot will synchronise all slash commands when it comes online. Make sure to
enable the required privileged gateway intents (at minimum the **Server
Members** intent if you plan to use the salary system).

## Data Files

The bot stores data in a set of JSON files created on first run:

- `users.json`: Account information and balances.
- `account_mapping.json`: Maps Discord user IDs to their account numbers.
- `transactions.json`: The latest 1,000 transactions.
- `public_accounts.json`: Metadata for public/shared accounts.
- `admin_settings.json`: Configuration for fees, taxes, salaries, and frozen
  accounts.

Back up these files regularly if you care about preserving account information
between deployments.

## Development Notes

- The codebase is structured around helper utilities and data classes to keep
  persistence logic separate from command handlers.
- Roblox-specific data models and commands were removed to simplify the bot and
  focus on Discord-native features.
- Pandas and OpenPyXL are used to export transaction history as an Excel file
  for administrators.

Feel free to customise the commands or extend the data structures to better fit
your community's needs.
