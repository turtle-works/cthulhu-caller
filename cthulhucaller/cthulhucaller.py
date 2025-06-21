import csv
import io
import math
import re

import aiohttp
import discord

from redbot.core import Config, commands

JUST_DIGITS = r"^\d+$"
GSHEET_URL_TEMPLATE = r"^https://docs.google.com/spreadsheets/d/e/[0-9A-Za-z-_]+/pub\?gid=0" + \
    r"&single=true&output=csv$"
GSHEET_URL_BASE = "https://docs.google.com/spreadsheets/d/e/{}/pub?gid=0&single=true&output=csv"

# huge pile of data-locating constants
# TODO: reorganize later. also maybe there's a better way?
DATA_LOCATIONS = {
    'name': (2, 1),
    'luck': (15, 2),
    'archetype': (2, 5),
    'psychic_power': (10, 5),
    'occupation_skill': (15, 5)
}

TALENT_LOCATIONS = [(7, 5), (8, 5)]

CHARACTERISTICS = ['str', 'con', 'siz', 'dex', 'app', 'edu', 'int', 'pow']
CHARACTERISTIC_ROW_START = 7
CHARACTERISTIC_COL = 2

SKILLS = ['accounting', 'animal handling', 'anthropology', 'appraise', 'archaeology', 'artillery',
    'charm', 'climb', 'credit rating', 'cthulhu mythos', 'demolitions', 'disguise', 'diving',
    'dodge', 'drive auto', 'electrical repair', 'fast talk', 'first aid', 'history', 'hypnosis',
    'intimidate', 'jump', 'language (own)', 'law', 'library use', 'listen', 'locksmith',
    'mechanical repair', 'medicine', 'natural world', 'navigate', 'occult',
    'operate heavy machinery', 'persuade', 'psychoanalysis', 'psychology', 'read lips', 'ride',
    'sleight of hand', 'spot hidden', 'stealth', 'swim', 'throw', 'track']
SKILL_ROW_START = 2
SKILL_COL = 8

BLOCK_LENGTH = 5

SPECIAL_ROW_STARTS = [3, 13, 23, 33]
SPECIAL_COL_STARTS = [10, 13]

CUSTOM_ROW_START = 3
CUSTOM_COL = 16

POINT_BUY_TOTAL = 460

# TODO: all possible default, valid, queryable skills and min/max for validation, or something
ALL_SKILL_MINS = {
    'accounting': 5,
    'animal handling': 5,
    'anthropology': 1,
    'appraise': 5,
    'archaeology': 1,
    'artillery': 1,
    'art/craft': 5,
    'axe': 15,
    'bow': 15,
    'brawl': 25,
    'chainsaw': 10,
    'charm': 15,
    'climb': 20,
    'credit rating': 0,
    'cthulhu mythos': 0,
    'demolitions': 1,
    'disguise': 5,
    'diving': 1,
    'dodge': 7,
    'drive auto': 20,
    'electrical repair': 10,
    'fast talk': 5,
    'first aid': 30,
    'flail': 10,
    'flamethrower': 10,
    'garrote': 15,
    'handgun': 20,
    'heavy weapons': 10,
    'history': 5,
    'hypnosis': 1,
    'intimidate': 15,
    'jump': 20,
    'language (other)': 1,
    'language (own)': 7,
    'law': 5,
    'library use': 20,
    'listen': 20,
    'locksmith': 1,
    'lore': 1,
    'machine gun': 10,
    'mechanical repair': 10,
    'medicine': 1,
    'natural world': 10,
    'navigate': 10,
    'occult': 5,
    'operate heavy machinery': 1,
    'persuade': 10,
    'pilot': 1,
    'psychoanalysis': 1,
    'psychology': 10,
    'read lips': 1,
    'ride': 5,
    'rifle/shotgun': 25,
    'science': 1,
    'sleight of hand': 10,
    'spear': 20,
    'spot hidden': 25,
    'stealth': 20,
    'submachine gun': 15,
    'survival': 10,
    'swim': 20,
    'sword': 20,
    'throw': 20,
    'track': 10,
    'whip': 5
}


class CthulhuCaller(commands.Cog):
    """Cog that lets users do simple things for Call of Cthulhu."""

    def __init__(self, bot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot

        self.config = Config.get_conf(self, identifier=2020567472)
        self.config.register_user(active_char=None, characters={}, csettings={}, preferences={})

    async def red_get_data_for_user(self, *, user_id):
        """Get a user's personal data."""
        characters = await self.config.user_from_id(user_id).characters()
        if characters:
            data = f"For user with ID {user_id}, data is stored for characters with " + \
                "published Google Sheet urls:\n" + \
                "\n".join([self.make_link_from_sheet_id(g_id) for g_id in characters.keys()])
        else:
            data = f"No data is stored for user with ID {user_id}.\n"
        return {"user_data.txt": BytesIO(data.encode())}

    async def red_delete_data_for_user(self, *, requester, user_id):
        """Delete a user's personal data.

        Imported Call of Cthulhu character data is stored by this cog.
        """
        await self.config.user_from_id(user_id).clear()

    @commands.command(name="import")
    async def import_char(self, ctx, url: str):
        """Import from the Google Sheet template."""
        if not re.match(GSHEET_URL_TEMPLATE, url):
            await ctx.send("Couldn't parse that as a link to a published-to-web Google Sheet.")
            return

        sheet_id = self._get_sheet_identifier_from_url(url)

        async with self.config.user(ctx.author).characters() as characters:
            if sheet_id in characters:
                active_char = await self.config.user(ctx.author).active_char()
                if sheet_id != active_char:
                    await ctx.send("This sheet has already been imported, but is not currently " + \
                        "active.")
                else:
                    await ctx.send("This sheet has already been imported and is currently active.")
                return

        await self.bot.wait_until_ready()
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                reader = csv.reader(io.StringIO(await response.text()), delimiter=',')

        raw_data = list(reader)
        char_data = self.read_char_data(raw_data)

        if not self._is_char_data_valid(char_data):
            # TODO: update message as more things are validated
            # TODO: add better feedback as to what exactly is incorrect
            await ctx.send("Something was wrong with this sheet. Please check that every cell " + \
                "is in the right place and filled in correctly. Aborting import.")
            return

        async with self.config.user(ctx.author).characters() as characters:
            characters[sheet_id] = char_data

        await self.config.user(ctx.author).active_char.set(sheet_id)

        balances = self._get_starting_balances(char_data)
        async with self.config.user(ctx.author).csettings() as csettings:
            csettings[sheet_id] = {}
            csettings[sheet_id]['balances'] = balances

        await ctx.send(f"Successfully imported data for {char_data['name']}.")

    def _get_sheet_identifier_from_url(self, url: str):
        start_index = url.find('/d/e/') + 5
        end_index = url.find('/pub')

        return url[start_index:end_index]

    def read_char_data(self, raw_data: list):
        char_data = {}

        # general data that doesn't fall into the other categories
        for key in DATA_LOCATIONS.keys():
            char_data[key] = raw_data[DATA_LOCATIONS[key][0]][DATA_LOCATIONS[key][1]]

        # talents
        char_data['talents'] = []
        for tup in TALENT_LOCATIONS:
            char_data['talents'].append(raw_data[tup[0]][tup[1]])

        # characteristics
        char_data['characteristics'] = {}
        for i in range(len(CHARACTERISTICS)):
            char_data['characteristics'][CHARACTERISTICS[i]] = \
                raw_data[CHARACTERISTIC_ROW_START + i][CHARACTERISTIC_COL]

        # skills without specializations
        char_data['skills'] = {}
        for i in range(len(SKILLS)):
            char_data['skills'][SKILLS[i]] = raw_data[SKILL_ROW_START + i][SKILL_COL]

        # specialization skills
        for i in range(len(SPECIAL_COL_STARTS)):
            for j in range(len(SPECIAL_ROW_STARTS)):
                for k in range(BLOCK_LENGTH):
                    # move down rows for the length of the block, selecting two adjacent values
                    skill = raw_data[SPECIAL_ROW_STARTS[j] + k][SPECIAL_COL_STARTS[i]]
                    points = raw_data[SPECIAL_ROW_STARTS[j] + k][SPECIAL_COL_STARTS[i] + 1]

                    if skill and points:
                        char_data['skills'][skill] = points

        # custom skills
        for i in range(BLOCK_LENGTH):
            skill = raw_data[CUSTOM_ROW_START + i][CUSTOM_COL]
            points = raw_data[CUSTOM_ROW_START + i][CUSTOM_COL + 1]

            if skill and points:
                char_data['skills'][skill] = points

        return char_data

    def _is_char_data_valid(self, char_data: dict):
        # characteristics should all be integers, multiples of 5, totalling to 460
        if not self._are_characteristics_valid(char_data['characteristics'], char_data['luck']):
            return False

        # talents are distinct and both present
        if not char_data['talents'][0] or not char_data['talents'][1] or \
            char_data['talents'][0] == char_data['talents'][1]:
            return False

        # skill values should all be integers
        for skill in char_data['skills'].keys():
            if not self._is_integer(char_data['skills'][skill]):
                return False

        # TODO: add data validation such as min/max
        return True

    def _are_characteristics_valid(self, characteristics: dict, luck: str):
        if not all([self._is_characteristic_valid(characteristics[ch]) for ch in CHARACTERISTICS]):
            return False
        if not self._is_characteristic_valid(luck):
            return False

        return sum([int(characteristics[ch]) for ch in CHARACTERISTICS]) + \
            int(luck) == POINT_BUY_TOTAL

    def _is_characteristic_valid(self, characteristic: str):
        return self._is_integer(characteristic) and int(characteristic) % 5 == 0
    
    def _is_integer(self, value: str):
        return re.match(JUST_DIGITS, value)

    def _get_starting_balances(self, char_data: dict):
        ch_pow = int(char_data['characteristics']['pow'])
        ch_con = int(char_data['characteristics']['con'])
        ch_siz = int(char_data['characteristics']['siz'])

        balances = {
            'luck': int(char_data['luck']),
            'sanity': ch_pow,
            'magic': ch_pow / 5,
            'magic_maximum': ch_pow / 5,
            'health': math.floor((ch_con + ch_siz) / 10),
            'health_maximum': math.floor((ch_con + ch_siz) / 10)
        }

        return balances

    @commands.group(aliases=["char"])
    async def character(self, ctx):
        """Commands for character management."""

    @character.command(name="list")
    async def character_list(self, ctx):
        """List all characters that the user has imported."""
        await self._character_list(ctx, False)

    @character.command(name="links")
    async def character_links(self, ctx):
        """Receive a list of all the characters that the user has imported, with data links."""
        await self._character_list(ctx, True)

    async def _character_list(self, ctx, send_links: bool):
        characters = await self.config.user(ctx.author).characters()
        if not characters:
            await ctx.send("You have no characters.")
            return

        active_id = await self.config.user(ctx.author).active_char()

        lines = []
        for sheet_id in characters.keys():
            char_data = characters[active_id]
            line = f"{char_data['name']}"
            if send_links:
                line += f" ([link]({self.make_link_from_sheet_id(sheet_id)}))"
            line += f" (**active**)" if sheet_id == active_id else ""
            lines.append(line)

        if not send_links:
            await ctx.send("Your characters:\n" + "\n".join(sorted(lines)))
        else:
            await ctx.author.send("Your characters:\n" + "\n".join(sorted(lines)))
            await ctx.send("List has been sent to your DMs.")

    def make_link_from_sheet_id(self, sheet_id: str):
        return GSHEET_URL_BASE.format(sheet_id)

    @character.command(name="remove", aliases=["delete"])
    async def character_remove(self, ctx, *, query: str):
        """Remove a character from the list.
        
        Takes a link to a published Google Sheet or a character name as argument.
        """
        async with self.config.user(ctx.author).characters() as characters:
            character_id = await self.sheet_id_from_query(ctx, query)

            if not character_id:
                await ctx.send(f"Could not find a character to match `{query}`.")
                return

            char_data = characters[character_id]
            characters.pop(character_id)

            async with self.config.user(ctx.author).csettings() as csettings:
                if character_id in csettings:
                    csettings.pop(character_id)

            if await self.config.user(ctx.author).active_char() == character_id:
                await self.config.user(ctx.author).active_char.set(None)

            await ctx.send(f"{char_data['name']} has been removed from your characters.")

    async def sheet_id_from_query(self, ctx, query: str):
        if re.match(GSHEET_URL_TEMPLATE, query):
            return self._get_sheet_identifier_from_url(query)
        else:
            query = query.lower()
            characters = await self.config.user(ctx.author).characters()

            for sheet_id in characters.keys():
                char_data = characters[sheet_id]
                if query in char_data['name'].lower():
                    return sheet_id

        return None
