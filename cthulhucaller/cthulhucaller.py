import csv
import io
import math
import random
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

SKILLS = ['Accounting', 'Animal Handling', 'Anthropology', 'Appraise', 'Archaeology', 'Artillery',
    'Charm', 'Climb', 'Credit Rating', 'Cthulhu Mythos', 'Demolitions', 'Disguise', 'Diving',
    'Dodge', 'Drive Auto', 'Electrical Repair', 'Fast Talk', 'First Aid', 'History', 'Hypnosis',
    'Intimidate', 'Jump', 'Language (Own)', 'Law', 'Library Use', 'Listen', 'Locksmith',
    'Mechanical Repair', 'Medicine', 'Natural World', 'Navigate', 'Occult',
    'Operate Heavy Machinery', 'Persuade', 'Psychoanalysis', 'Psychology', 'Read Lips', 'Ride',
    'Sleight of Hand', 'Spot Hidden', 'Stealth', 'Swim', 'Throw', 'Track']
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
    'Accounting': 5,
    'Animal Handling': 5,
    'Anthropology': 1,
    'Appraise': 5,
    'Archaeology': 1,
    'Artillery': 1,
    'Art and Craft': 5,
    'Axe': 15,
    'Bow': 15,
    'Brawl': 25,
    'Chainsaw': 10,
    'Charm': 15,
    'Climb': 20,
    'Credit Rating': 0,
    'Cthulhu Mythos': 0,
    'Demolitions': 1,
    'Disguise': 5,
    'Diving': 1,
    'Dodge': 7,
    'Drive Auto': 20,
    'Electrical Repair': 10,
    'Fast Talk': 5,
    'First Aid': 30,
    'Flail': 10,
    'Flamethrower': 10,
    'Garrote': 15,
    'Handgun': 20,
    'Heavy Weapons': 10,
    'History': 5,
    'Hypnosis': 1,
    'Intimidate': 15,
    'Jump': 20,
    'Language (Other)': 1,
    'Language (Own)': 7,
    'Law': 5,
    'Library Use': 20,
    'Listen': 20,
    'Locksmith': 1,
    'Lore': 1,
    'Machine Gun': 10,
    'Mechanical Repair': 10,
    'Medicine': 1,
    'Natural World': 10,
    'Navigate': 10,
    'Occult': 5,
    'Operate Heavy Machinery': 1,
    'Persuade': 10,
    'Pilot': 1,
    'Psychoanalysis': 1,
    'Psychology': 10,
    'Read Lips': 1,
    'Ride': 5,
    'Rifle/Shotgun': 25,
    'Science': 1,
    'Sleight of Hand': 10,
    'Spear': 20,
    'Spot Hidden': 25,
    'Stealth': 20,
    'Submachine Gun': 15,
    'Survival': 10,
    'Swim': 20,
    'Sword': 20,
    'Throw': 20,
    'Track': 10,
    'Whip': 5
}

UMBRELLA_SKILLS = ['Art and Craft', 'Language (Other)', 'Lore', 'Pilot', 'Science', 'Survival']

DAMAGE_BONUS = 0
BUILD = 1
DAMAGE_BUILD_CHART = {
    64: [-2, -2],
    84: [-1, -1],
    124: [0, 0],
    164: ["1d4", 1],
    204: ["1d6", 2],
    284: ["2d6", 3],
    364: ["3d6", 4],
    444: ["4d6", 5],
    524: ["5d6", 6],
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
        ch_con = int(char_data['characteristics']['con'])
        ch_siz = int(char_data['characteristics']['siz'])
        ch_pow = int(char_data['characteristics']['pow'])

        balances = {
            'luck': int(char_data['luck']),
            'sanity': ch_pow,
            'magic': math.floor(ch_pow / 5),
            'magic_maximum': math.floor(ch_pow / 5),
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
            char_data = characters[sheet_id]
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

    @character.command(name="setactive", aliases=["set", "switch"])
    async def character_set(self, ctx, *, query: str):
        """Set the active character.

        Takes a link to a published Google Sheet or a character name as argument.
        """
        characters = await self.config.user(ctx.author).characters()

        character_id = await self.sheet_id_from_query(ctx, query)

        if not character_id:
            await ctx.send(f"Could not find a character to match `{query}`.")
            return

        await self.config.user(ctx.author).active_char.set(character_id)
        char_data = characters[character_id]
        await ctx.send(f"{char_data['name']} made active.")

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

    @commands.command()
    async def sheet(self, ctx):
        """Show the active character's sheet."""
        sheet_id = await self.config.user(ctx.author).active_char()
        if sheet_id is None:
            await ctx.send("No character is active. `import` a new character or switch to an " + \
                "existing one with `character setactive`.")
            return

        data = await self.config.user(ctx.author).characters()
        char_data = data[sheet_id]
        characteristics = char_data['characteristics']
        skills = char_data['skills']

        settings = await self.config.user(ctx.author).csettings()
        balances = settings[sheet_id]['balances']

        embed = await self._get_base_embed(ctx)
        embed.title = f"{char_data['name']}"

        desc_lines = []
        desc_lines.append(f"{char_data['archetype']}")
        desc_lines.append(f"**Luck**: {balances['luck']}")
        desc_lines.append(f"**Sanity**: {balances['sanity']}")
        desc_lines.append(f"**Health**: {balances['health']}/{balances['health_maximum']}")
        desc_lines.append(f"**Magic**: {balances['magic']}/{balances['magic_maximum']}")

        damage_bonus, build, movement = self.calculate_damage_build_mov(characteristics)
        desc_lines.append(f"**Damage Bonus**: {damage_bonus} **Build**: {build} " + \
            f"**Move Rate**: {movement}")
        embed.description = "\n".join(desc_lines)

        characteristic_lines = []
        for ch in characteristics.keys():
            characteristic_lines.append(f"**{ch.upper()}**: {characteristics[ch].zfill(2)}")
        characteristic_field = " ".join(characteristic_lines[:4]) + "\n" + \
            " ".join(characteristic_lines[4:])
        embed.add_field(name="Characteristics", value=characteristic_field, inline=False)

        skill_lines = []
        for skill in skills.keys():
            skill_lines.append(f"{skill}: {skills[skill].zfill(2)}")

        default_lines = skill_lines[:len(SKILLS)]
        custom_lines = skill_lines[len(SKILLS):]

        for skill in UMBRELLA_SKILLS:
            default_lines.append(f"{skill}: {str(ALL_SKILL_MINS[skill]).zfill(2)}")

        half_count = math.floor((len(SKILLS) + len(UMBRELLA_SKILLS)) / 2)
        skill_field_1 = "\n".join(sorted(default_lines)[:half_count])
        skill_field_2 = "\n".join(sorted(default_lines)[half_count:])
        custom_field = "\n".join(sorted(custom_lines))
        embed.add_field(name="Skills", value=skill_field_1, inline=True)
        embed.add_field(name="Skills (cont.)", value=skill_field_2, inline=True)
        embed.add_field(name="Custom Skills", value=custom_field, inline=True)

        await ctx.send(embed=embed)

    async def _get_base_embed(self, ctx):
        embed = discord.Embed()

        sheet_id = await self.config.user(ctx.author).active_char()
        settings = await self.config.user(ctx.author).csettings()

        if sheet_id in settings and 'color' in settings[sheet_id] and settings[sheet_id]['color']:
            embed.colour = discord.Colour(settigns[sheet_id]['color'])
        else:
            embed.colour = discord.Colour(random.randint(0x000000, 0xFFFFFF))

        if sheet_id in settings and 'image_url' in settings[sheet_id] and \
            settings[sheet_id]['image_url']:
            embed.set_thumbnail(url=settings[sheet_id]['image_url'])

        return embed

    def calculate_damage_build_mov(self, characteristics: dict):
        ch_str = int(characteristics['str'])
        ch_dex = int(characteristics['dex'])
        ch_siz = int(characteristics['siz'])

        for upper_thresh in DAMAGE_BUILD_CHART.keys():
            if ch_str + ch_siz <= upper_thresh:
                damage_bonus = DAMAGE_BUILD_CHART[upper_thresh][DAMAGE_BONUS]
                build = DAMAGE_BUILD_CHART[upper_thresh][BUILD]
                break

        movement = 7 if ch_str < ch_siz and ch_dex < ch_siz else \
            9 if ch_str > ch_siz and ch_dex > ch_siz else 8

        return damage_bonus, build, movement
