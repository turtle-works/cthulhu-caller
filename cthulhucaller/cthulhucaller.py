import csv
import io
import math
import random
import re

import aiohttp
import discord
import d20

from redbot.core import Config, commands

GSHEET_URL_TEMPLATE = r"^https://docs.google.com/spreadsheets/d/e/[0-9A-Za-z-_]+/pub\?gid=0" + \
    r"&single=true&output=csv$"
GSHEET_URL_BASE = "https://docs.google.com/spreadsheets/d/e/{}/pub?gid=0&single=true&output=csv"

KNOWN_FLAGS = ["bonus", "penalty", "phrase", "rr"]
DOUBLE_QUOTES = ["\"", "“", "”"]

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
        if not self._is_char_csv_data(raw_data):
            await ctx.send("Couldn't find character data at this link. Is the sheet still " + \
                "being published to web?")
            return

        char_data = self.read_char_data(raw_data)

        is_valid, errors = self._is_char_data_valid(char_data)
        if not is_valid:
            await ctx.send(f"Something was wrong with this sheet: {'; '.join(errors)}.\n" + \
                "Please check that every cell is in the right place and filled in correctly. " + \
                "Aborting import.")
            return

        async with self.config.user(ctx.author).characters() as characters:
            characters[sheet_id] = char_data

        await self.config.user(ctx.author).active_char.set(sheet_id)

        balances = self._get_starting_balances(char_data)
        async with self.config.user(ctx.author).csettings() as csettings:
            csettings[sheet_id] = {}
            csettings[sheet_id]['balances'] = balances

        await ctx.send(f"Successfully imported data for {char_data['name']}.")

    @commands.command()
    async def update(self, ctx, url: str=""):
        """Update character data.
        
        Optionally takes a link to a published Google Sheet as argument.
        """
        active_sheet_id = await self.config.user(ctx.author).active_char()
        if not url:
            sheet_id = active_sheet_id
            if sheet_id is None:
                await ctx.send("Tried to update active character but no character is active.")
                return

            char_url = self.make_link_from_sheet_id(sheet_id)
        else:
            if not re.match(GSHEET_URL_TEMPLATE, url):
                await ctx.send("Couldn't parse that as a link to a published-to-web Google Sheet.")
                return
            sheet_id = self._get_sheet_identifier_from_url(url)
            char_url = url

            characters = await self.config.user(ctx.author).characters()
            if sheet_id not in characters.keys():
                await ctx.send("This character was not recognized, `import` them instead.")
                return
            else:
                if sheet_id != active_sheet_id:
                    await ctx.send("Making this character active and updating.")
                await self.config.user(ctx.author).active_char.set(sheet_id)

        await self.bot.wait_until_ready()
        async with aiohttp.ClientSession() as session:
            async with session.get(char_url) as response:
                reader = csv.reader(io.StringIO(await response.text()), delimiter=',')

        raw_data = list(reader)
        if not self._is_char_csv_data(raw_data):
            await ctx.send("Couldn't find character data at this link. Is the sheet still " + \
                "being published to web?")
            return

        char_data = self.read_char_data(raw_data)

        is_valid, errors = self._is_char_data_valid(char_data)
        if not is_valid:
            await ctx.send(f"Something was wrong with this sheet: {'; '.join(errors)}.\n" + \
                "Please check that every cell is in the right place and filled in correctly. " + \
                "Aborting import.")
            return

        async with self.config.user(ctx.author).characters() as characters:
            characters[sheet_id] = char_data

        await self.config.user(ctx.author).active_char.set(sheet_id)

        # balances should stay the same unless max values were changed by this update
        # patch_notes = []
        balance_updates = []
        async with self.config.user(ctx.author).csettings() as settings:
            balances = settings[sheet_id]['balances']
            new_balances = self._get_starting_balances(char_data)

            balances['magic_maximum'] = new_balances['magic_maximum']
            balances['health_maximum'] = new_balances['health_maximum']

            if new_balances['magic_maximum'] < balances['magic']:
                balance_updates.append(f"Magic was {balances['magic']} and has been reduced " + \
                    f"to the new maximum of {new_balances['magic_maximum']}.")
                balances['magic'] = new_balances['magic_maximum']

            if new_balances['health_maximum'] < balances['health']:
                balance_updates.append(f"Health was {balances['health']} and has been reduced " + \
                    f"to the new maximum of {new_balances['health_maximum']}.")

            new_sanity_max = 99 - int(char_data['skills']['Cthulhu Mythos'])
            if balances['sanity'] > new_sanity_max:
                balance_updates.append(f"Sanity was {balances['sanity']} and has been reduced " + \
                    f"to the new maximum of {new_sanity_max}.")
                balances['sanity'] = new_sanity_max

        balance_update_text = f"\n{' '.join(balance_updates)}" if len(balance_updates) else ""

        await ctx.send(f"Updated data for {char_data['name']}.{balance_update_text}")

    def _get_sheet_identifier_from_url(self, url: str):
        start_index = url.find('/d/e/') + 5
        end_index = url.find('/pub')

        return url[start_index:end_index]

    def _is_char_csv_data(self, raw_data: list):
        # TODO: think of other validations
        return len(raw_data) == 46 and "!DOCTYPE html" not in raw_data[0]

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
        errors = set()
        # characteristics should all be integers, multiples of 5, totalling to 460
        if not self._are_characteristics_valid(char_data['characteristics'], char_data['luck']):
            errors.add("characteristics should all be multiples of 5 and total to 460")

        # name, archetype, occupation stat should be populated
        if not char_data['name'] or not char_data['archetype'] or \
            not char_data['occupation_skill']:
            errors.add("all cells should be filled out")

        # talents are distinct and both present
        if not char_data['talents'][0] or not char_data['talents'][1] or \
            char_data['talents'][0] == char_data['talents'][1]:
            errors.add("talents should both be selected and different from one another")

        if "Psychic Power" in char_data['talents'] and not char_data['psychic_power']:
            errors.add("psychic power should be selected if the talent is chosen")

        # skill values should all be integers
        for skill in char_data['skills'].keys():
            if not char_data['skills'][skill].isnumeric():
                errors.add("skills should all be integers")
            elif skill in ALL_SKILL_MINS:
                if not ALL_SKILL_MINS[skill] <= int(char_data['skills'][skill]) <= 99:
                    errors.add("skills should be between minimum value and 99")
            else:
                if not int(char_data['skills'][skill]) <= 99:
                    errors.add("skills should not exceed 99")

        if len(errors) > 0:
            return False, errors
        else:
            return True, None

    def _are_characteristics_valid(self, characteristics: dict, luck: str):
        if not all([self._is_characteristic_valid(characteristics[ch]) for ch in CHARACTERISTICS]):
            return False
        if not self._is_characteristic_valid(luck):
            return False

        return sum([int(characteristics[ch]) for ch in CHARACTERISTICS]) + \
            int(luck) == POINT_BUY_TOTAL

    def _is_characteristic_valid(self, characteristic: str):
        return characteristic.isnumeric() and int(characteristic) % 5 == 0
    
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

    @character.command(name="setcolor")
    async def character_color(self, ctx, *, color: str):
        """Set the active character's embed color.

        Takes either a hex code or "random". Examples:
        `[p]character setcolor #FF8822`
        `[p]character setcolor random`
        """
        sheet_id = await self.config.user(ctx.author).active_char()
        if sheet_id is None:
            await ctx.send("No character is active. `import` a new character or switch to an " + \
                "existing one with `character setactive`.")
            return

        data = await self.config.user(ctx.author).characters()
        name = data[sheet_id]['name']

        if re.match(r"^#?[0-9a-fA-F]{6}$", color) or color.lower() == "random":
            async with self.config.user(ctx.author).csettings() as csettings:
                if color.lower() == "random":
                    csettings[sheet_id]['color'] = None
                else:
                    csettings[sheet_id]['color'] = int(color.lstrip("#"), 16)
            embed = await self._get_base_embed(ctx)
            embed.description = f"Embed color has been set for {name}."
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Could not interpret `{color}` as a color.")

    @character.command(name="setimage")
    async def character_image(self, ctx, *, image: str=""):
        """Set the active character's image.

        Takes either an attached image, an image link, or "none" (to delete).
        """
        sheet_id = await self.config.user(ctx.author).active_char()
        if sheet_id is None:
            await ctx.send("No character is active. `import` a new character or switch to an " + \
                "existing one with `character setactive`.")
            return

        data = await self.config.user(ctx.author).characters()
        name = data[sheet_id]['name']

        if len(ctx.message.attachments):
            if not ctx.message.attachments[0].content_type.startswith("image"):
                await ctx.send("Could not interpret the attachment as an image.")
                return
            else:
                url = ctx.message.attachments[0].url
        else:
            if not image:
                await ctx.send("Could not interpret image link.")
                return
            elif image == "none":
                url = None
            else:
                url = image

        async with self.config.user(ctx.author).csettings() as csettings:
            csettings[sheet_id]['image_url'] = url

        embed = await self._get_base_embed(ctx)
        embed.description = f"Image has been set for {name}."

        await ctx.send(embed=embed)

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

    @commands.command(aliases=["c", "roll", "r"])
    async def check(self, ctx, *, query):
        """Make a d100 roll.
        
        Takes a plain DC as argument, or a check name (to make the roll as the active character).
        """
        processed_query = self.process_query(query)

        dc = None
        skill = None
        data = await self.config.user(ctx.author).characters()

        if processed_query['query'].isnumeric():
            dc = int(processed_query['query'])
            char_data = None
        else:
            sheet_id = await self.config.user(ctx.author).active_char()
            if sheet_id is None:
                await ctx.send("No character is active. `import` a new character or switch to an " + \
                    "existing one with `character setactive`.")
                return

            char_data = data[sheet_id]
            settings = await self.config.user(ctx.author).csettings()
            balances = settings[sheet_id]['balances']

            check_name = processed_query['query'].lower()
            dc, skill = self.find_skill(check_name, char_data, balances)

            talent_bonus = self._get_talent_bonus(char_data['talents'], skill)
            if talent_bonus:
                processed_query['bonus'].append("1")

        if dc is None:
            await ctx.send(f"Could not understand `{check_name}`.")
            return

        bonus_str = self._get_rollable_arg(processed_query['bonus'])
        penalty_str = self._get_rollable_arg(processed_query['penalty'])
        phrase_str = "\n".join(processed_query['phrase'])
        repetition_str = self._get_single_rollable_arg(processed_query['rr'])

        dc_str = f"({dc}/{math.floor(dc / 2)}/{math.floor(dc / 5)})"

        if skill is not None:
            name = char_data['name']
            article = "an" if skill.lower()[0] in ["a", "e", "i", "o", "u"] else "a"
            title_text = f"{name} makes {article} {skill} {dc_str} roll!"
        else:
            title_text = f"DC {dc_str} roll!"

        embed = await self._get_base_embed(ctx)
        embed.title = title_text

        if not repetition_str or d20.roll(repetition_str).total == 1:
            roll_text, degree_text, luck_text = self.perform_skill_roll(dc, bonus_str, penalty_str)
            description = f"{degree_text}\n{roll_text}"

            if phrase_str:
                embed.description = f"{description}\n*> {phrase_str}*"
            else:
                embed.description = description
            embed.set_footer(text=luck_text)
        else:
            if phrase_str:
                embed.description = f"> *{phrase_str}*"

            for i in range(d20.roll(repetition_str).total):
                roll_text, degree_text, luck_text = \
                    self.perform_skill_roll(dc, bonus_str, penalty_str)
                field_name = f"Roll {i + 1}"
                embed.add_field(name=field_name, value=f"{degree_text}{luck_text}\n{roll_text}")

        await ctx.send(embed=embed)

    def process_query(self, query_str: str):
        processed_flags = self._get_base_flags()

        # prepend a space so the flag finding will succeed even with no query. hey, if it works...
        # also append a space so argless flags at the end won't poison the previous flag's arg
        query_str = " " + query_str + " "

        flag_locs = []
        search_start = 0
        while search_start < len(query_str):
            # looks for instances of all the flags simultaneously
            next_flags = [query_str.find(f" -{flag} ", search_start) for flag in KNOWN_FLAGS]

            # no more flags, end loop
            if all([f < 0 for f in next_flags]):
                break
            # save location of earliest flag
            else:
                while -1 in next_flags:
                    next_flags.remove(-1)
                next_flag = min(next_flags)
                flag_locs.append(next_flag)
                search_start = next_flag + 2
        flag_locs.sort()

        if not flag_locs:
            processed_flags['query'] = query_str.strip()
        else:
            processed_flags['query'] = query_str[:flag_locs[0]].strip()

        for i in range(len(flag_locs)):
            if i == len(flag_locs) - 1:
                flag_and_arg = query_str[flag_locs[i]:]
            else:
                flag_and_arg = query_str[flag_locs[i]:flag_locs[i + 1]]

            flag_and_arg = flag_and_arg.strip()[1:]
            # split only on the first space, if it exists
            flag_and_arg = flag_and_arg.split(" ", 1)

            flag = flag_and_arg[0]
            if len(flag_and_arg) > 1:
                arg = flag_and_arg[1]
                # TODO: maybe give this another try later. for now, flags (with -) only
                # arg_str = flag_and_arg[1]

                # # if this begins with a double quote, the arg ends at the final double quote
                # if len(arg_str) > 1 and arg_str.strip()[0] in DOUBLE_QUOTES:
                #     end = max([arg_str.rfind(q) for q in DOUBLE_QUOTES])
                # # if not, the arg ends after one word
                # else:
                #     end = arg_str.find(" ") if " " in arg_str else len(arg_str)
                # arg = arg_str[:end]

                # # search what remains for additional bonus/penalty indicators
                # search_str = arg_str[end:].lower()
                # if "bonus" in search_str or "adv" in search_str:
                #     processed_flags['bonus'].append("1")
                # if "penalty" in search_str or "dis" in search_str:
                #     processed_flags['penalty'].append("1")
            else:
                arg = ""

            arg = arg.strip()
            if len(arg) > 1 and arg[0] in DOUBLE_QUOTES and arg[-1] in DOUBLE_QUOTES:
                arg = arg[1:-1]

            processed_flags[flag].append(arg)

        return processed_flags

    def _get_base_flags(self):
        processed_flags = {'query': ""}
        for flag in KNOWN_FLAGS:
            processed_flags[flag] = []
        return processed_flags

    def find_skill(self, check_name: str, char_data: dict, balances: dict):
        for ch in char_data['characteristics'].keys():
            if check_name in ch.lower():
                return int(char_data['characteristics'][ch]), ch.upper()

        for sk in char_data['skills'].keys():
            if check_name in sk.lower():
                return int(char_data['skills'][sk]), sk

        if check_name == "know":
            return int(char_data['characteristics']['edu']), "Know"

        if check_name == "idea":
            return int(char_data['characteristics']['int']), "Idea"

        if check_name == "luck":
            return int(balances['luck']), "Luck"

        if check_name in "spellcasting":
            return int(char_data['characteristics']['pow']), "Spellcasting"

        for sk in UMBRELLA_SKILLS:
            if check_name in sk.lower():
                return ALL_SKILL_MINS[sk], sk

        return None, None

    # for a flag that should only have been used once
    def _get_single_rollable_arg(self, args: list):
        try:
            d20.roll(args[0])
            return args[0]
        # should catch both empty list and not rollable
        except:
            return ""

    def _get_rollable_arg(self, args: list):
        rollable_args = []
        for arg in args:
            # perhaps hacky but it works
            try:
                d20.roll(arg)
                rollable_args.append(arg)
            except:
                # this was invalid and not rollable, don't use it
                pass
        return " + ".join(rollable_args)

    def _get_talent_bonus(self, talents: list, skill: str):
        if "Animal Companion" in talents and skill == "Animal Handling" or \
            "Arcane Insight" in talents and skill == "Spellcasting" or \
            "Endurance" in talents and skill == "CON" or \
            "Keen Hearing" in talents and skill == "Listen" or \
            "Keen Vision" in talents and skill == "Spot Hidden" or \
            "Linguist" in talents and "Language" in skill or \
            "Photographic Memory" in talents and skill == "Know" or \
            "Power Lifter" in talents and skill == "STR" or \
            "Sharp Witted" in talents and skill == "INT" or \
            "Smooth Talker" in talents and skill == "Charm" or \
            "Strong Willed" in talents and skill == "POW":
            return 1
        else:
            return 0

    def perform_skill_roll(self, dc: int, bonus_str: str, penalty_str: str):
        bonus = d20.roll(bonus_str).total if bonus_str else 0
        penalty = d20.roll(penalty_str).total if penalty_str else 0
        net_dice = bonus - penalty

        if net_dice == 0:
            # no need to show extra dice, simplify to a d100
            hundreds = d20.roll("1d100")
            roll_total = hundreds.total
            roll_text = str(hundreds)
        else:
            # tens is 0-indexed: 00 through 90
            tens = d20.roll(self.make_tens_string(net_dice))
            # ones is 1-indexed: 01 through 10
            ones = d20.roll("1d10")
            roll_total = (tens.total * 10) + ones.total
            roll_text = f"{str(tens)}, {str(ones)} -> `{roll_total}`"

        to_success, to_hard, to_extreme, degree_of_success = \
            self._get_degree_of_success(dc, roll_total)
        degree_text = f"{degree_of_success}"

        luck_strs = []
        if to_success is not None:
            luck_strs.append(f"{to_success} Luck to Regular")
        if to_hard is not None:
            luck_strs.append(f"{to_hard} Luck to Hard")
        if to_extreme is not None:
            luck_strs.append(f"{to_extreme} Luck to Extreme")
        luck_str = ", ".join(luck_strs)
        luck_text = " (" + luck_str + ")" if luck_str else ""

        return roll_text, degree_text, luck_text

    def make_tens_string(self, net_dice: int):
        if net_dice > 0:
            return f"{abs(net_dice) + 1}d10kl1 - 1"
        else:
            return f"{abs(net_dice) + 1}d10kh1 - 1"

    def _get_degree_of_success(self, dc: int, roll_total: int):
        extreme_dc = math.floor(dc / 5)
        hard_dc = math.floor(dc / 2)

        if roll_total == 1:
            return None, None, None, "**Critical Success**"
        elif roll_total <= extreme_dc:
            return None, None, None, "**Extreme Success**"
        elif roll_total <= hard_dc:
            return None, None, roll_total - extreme_dc, "**Hard Success**"
        elif roll_total <= dc:
            return None, roll_total - hard_dc, roll_total - extreme_dc, "**Regular Success**"
        elif roll_total > 99:
            return None, None, None, "**Fumble**"
        elif roll_total >= 96:
            return None, None, None, "**Fumble** (if success requires a result below 50)"
        else:
            return roll_total - dc, roll_total - hard_dc, roll_total - extreme_dc, "**Failure**"

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
        desc_lines.append(f"**Talents**: {', '.join(char_data['talents'])}")
        if "Psychic Power" in char_data['talents'] and char_data['psychic_power']:
            desc_lines.append(f"**Psychic Power**: {char_data['psychic_power']}")

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
            embed.colour = discord.Colour(settings[sheet_id]['color'])
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

    @commands.group(aliases=["g"])
    async def game(self, ctx):
        """Commands for gameplay management."""

    @game.command()
    async def luck(self, ctx, *, amount: str=""):
        """Update the active character's luck.
        
        Takes an (integer or dice) amount to change by, "set #", or "max". Examples:
        `[p]game luck -8`
        `[p]game luck set 25`
        `[p]game luck max`
        """
        await self.modify_balance(ctx, amount, "luck")

    @game.command(aliases=["san"])
    async def sanity(self, ctx, *, amount: str=""):
        """Update the active character's sanity.
        
        Takes an (integer or dice) amount to change by, "set #", or "max". Examples:
        `[p]game sanity -1d6`
        `[p]game sanity set 55`
        `[p]game sanity max`
        """
        await self.modify_balance(ctx, amount, "sanity")

    @game.command(aliases=["hp"])
    async def health(self, ctx, *, amount: str=""):
        """Update the active character's health.
        
        Takes an (integer or dice) amount to change by, "set #", or "max". Examples:
        `[p]game health +3`
        `[p]game health set 8`
        `[p]game health max`
        """
        await self.modify_balance(ctx, amount, "health")

    @game.command()
    async def magic(self, ctx, *, amount: str=""):
        """Update the active character's magic points.
        
        Takes an (integer or dice) amount to change by, "set #", or "max". Examples:
        `[p]game magic +1d4`
        `[p]game magic set 10`
        `[p]game magic max`
        """
        await self.modify_balance(ctx, amount, "magic")

    async def modify_balance(self, ctx, amount: str, value_type: str):
        sheet_id = await self.config.user(ctx.author).active_char()
        if sheet_id is None:
            await ctx.send("No character is active. `import` a new character or switch to an " + \
                "existing one with `character setactive`.")
            return

        data = await self.config.user(ctx.author).characters()
        char_data = data[sheet_id]

        async with self.config.user(ctx.author).csettings() as settings:
            balances = settings[sheet_id]['balances']
            curr_value = balances[value_type]

            if value_type == "luck":
                max_value = 99
            elif value_type == "sanity":
                max_value = 99 - int(char_data['skills']['Cthulhu Mythos'])
            elif value_type == "health":
                max_value = balances['health_maximum']
            elif value_type == "magic":
                max_value = balances['magic_maximum']

            if amount == "max":
                new_value = max_value
            elif amount.startswith("set ") and len(amount) > 4:
                try:
                    new_value = min(max_value, max(0, d20.roll(amount[4:]).total))
                except:
                    await ctx.send("Could not interpret that amount as an integer or dice roll.")
                    return
            elif amount:
                try:
                    delta = d20.roll(amount).total
                    if delta < 0:
                        new_value = max(0, curr_value + delta)
                    else:
                        new_value = min(max_value, curr_value + delta)
                except:
                    await ctx.send("Could not interpret that amount as an integer or dice roll.")
                    return
            else:
                # no amount was given, just display
                output = f"{value_type.capitalize()}: {curr_value}"
                if value_type == "health" or value_type == "magic":
                    output += f"/{balances[f'{value_type}_maximum']}"
                await ctx.send(output)
                return

            balances[value_type] = new_value

            value_diff = new_value - curr_value
            op = "" if value_diff < 0 else "+"

            output = f"{value_type.capitalize()}: {new_value}"
            if value_type == "health" or value_type == "magic":
                output += f"/{balances[f'{value_type}_maximum']}"
            output += f" ({op}{value_diff})"

            await ctx.send(output)

    @commands.command(aliases=["downtime", "progress", "progression"])
    async def improve(self, ctx, *, query):
        """Roll for improvements to five skill DCs.
        
        Takes five space-separated integers as argument.
        """
        skills = query.split(" ")
        if len(skills) < 5 or not all([sk.isnumeric() for sk in skills[:5]]):
            await ctx.send("Could not read input as five space-separated integers.")
            return

        hundred_rolls = [d20.roll("1d100") for i in range(5)]
        improvements = [d20.roll("1d10") if hundred_rolls[i].total > int(skills[i]) else None \
            for i in range(5)]

        embed = await self._get_base_embed(ctx)
        embed.title = "Skill Improvement rolls!"
        for i in range(5):
            field_text = f"{skills[i]}"
            field_text += "" if improvements[i] is None else \
                f" -> **{int(skills[i]) + improvements[i].total}**"

            field_text += f"\n{str(hundred_rolls[i])}, "
            field_text += "failure." if improvements[i] is None else \
                f"success: {str(improvements[i])}"

            embed.add_field(name=f"Skill {i + 1}", value=field_text, inline=False)
        
        await ctx.send(embed=embed)
