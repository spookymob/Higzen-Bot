from bot import TOKEN, bot


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to a .env file.")
    bot.run(TOKEN)
