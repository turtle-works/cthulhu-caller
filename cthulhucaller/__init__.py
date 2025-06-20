from .cthulhucaller import CthulhuCaller

__red_end_user_data_statement__ = "No personal data is stored."


async def setup(bot):
    n = CthulhuCaller(bot)
    if not __import__('asyncio').iscoroutinefunction(bot.add_cog):
        bot.add_cog(n)
    else:
        await bot.add_cog(n)
