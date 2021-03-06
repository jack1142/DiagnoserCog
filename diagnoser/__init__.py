from redbot.core.bot import Red

from .diagnoser import Diagnoser

__red_end_user_data_statement__ = (
    "This cog does not persistently store data or metadata about users."
)


def setup(bot: Red) -> None:
    cog = Diagnoser(bot)
    bot.add_cog(cog)
