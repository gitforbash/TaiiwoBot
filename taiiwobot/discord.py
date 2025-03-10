import asyncio
import discord
import time
from bson import Int64

Empty = discord.Embed.Empty

from . import util
from .server import Server

import time


class Discord(Server):
    def __init__(self, config):
        missing_keys = util.missing_keys(["api_key"], config)
        if missing_keys:
            quit("[E] Missing args: %s. Check config.json" % (", ").join(missing_keys))
        defaults = {}
        self.type = "discord"
        defaults.update(config)
        self.config = defaults
        self.callbacks = {}
        self.reaction_callbacks = {}
        self.message_callbacks = {}
        self.followed_messages = {}
        intents = discord.Intents.default()
        intents.members = True
        self.client = discord.Client(intents=intents)

        # some logging handlers
        @self.on("message", "root")
        def log_message(message):
            m = message.raw_message
            util.debug(
                "[%s] @%s #%s <%s> %s"
                % (
                    time.strftime("%H:%M:%S"),
                    m.guild.name if m.guild else m.author.name,
                    m.channel.name if str(m.channel.type) != "private" else "DM",
                    m.author.name,
                    m.content,
                )
            )

        @self.client.event
        async def on_connect():
            self.name = self.client.user.name
            self.trigger("ready", True)

        @self.client.event
        async def on_ready():
            print("Finished loading members.")

        @self.client.event
        async def on_message(message):
            # for each message callback
            message_callbacks = self.message_callbacks.copy()
            for callback_id, callback in self.message_callbacks.items():
                # are we waiting for this message?
                if callback_id == message.channel.id + message.author.id:
                    # run the message callback
                    callback[1](message)
                    del message_callbacks[callback_id]
                    # return here to not invoke other plugins with awaited messages
                    self.message_callbacks = message_callbacks
                    return
                timeout = callback[2] if len(callback) > 2 else 60.0
                # check if the message callback is too old
                if time.time() - callback[0] > timeout:
                    # remove the message callback
                    del message_callbacks[callback_id]
            self.message_callbacks = message_callbacks

            message = self.format_message(message)
            self.trigger("message", message)

        @self.client.event
        async def on_message_delete(message):
            message = self.format_message(message)
            self.trigger("message-delete", message)

        @self.client.event
        async def on_message_edit(before, after):
            # fix a weird quirk where after.author is always user instead of member
            after.author = before.author
            # if the message is being followed, treat it as a new message
            # if before.id in self.followed_messages:
            await on_message(after)
            before = self.format_message(before)
            after = self.format_message(after)
            self.trigger("message-edit", before, after)

        @self.client.event
        async def on_reaction_add(reaction, reactor):
            self.trigger("reaction", reaction, reactor)
            # do we have any code to run in response to this?
            if reaction.message.id in self.reaction_callbacks:
                user, reactions = self.reaction_callbacks[reaction.message.id]
                if user and user != reactor.id:
                    return False
                for reaction_emoji, function in reactions:
                    if reaction.emoji == reaction_emoji:
                        function(
                            {
                                "emoji": reaction.emoji,
                                "reactor": reactor.id,
                                "message": reaction.message.id,
                                "channel": reaction.message.channel.id,
                            }
                        )
                        # if it was a targeted callback, remove it
                        if user:
                            # remove reactions
                            calls = []
                            for reaction_emoji, x in reactions:
                                calls.append(
                                    [
                                        reaction.message.remove_reaction,
                                        (reaction_emoji, self.client.user),
                                        {},
                                    ]
                                )
                            self.gaysyncio(calls)
                            # remove callbacks
                            del self.reaction_callbacks[reaction.message.id]
                        break

    def start(self):
        print("starting discord...")
        self.client.run(self.config["api_key"])

    def code_block(self, text):
        return "```" + text + "```"

    def is_owner(self, message):
        if "owner" in self.config and message.author == self.config["owner"]:
            return True
        elif message.raw_message.guild.owner.id == message.author:
            return True
        else:
            return False

    def is_mod(self, message):
        for role in message.raw_message.author.roles:
            # allow the bot owner
            if message.author == self.config["owner"]:
                return True
            # allow users with the specified roles
            if str(message.server) in self.config["plugin_config"]:
                server_config = self.config["plugin_config"][str(message.server)]
            else:
                # TODO: this should be a RuntimeError but I couldn't be bothered
                self.msg(
                    message.target,
                    "This server has no authenticated roles assigned. "
                    + "Please get the server owner to add one with `$mod set-role @role`",
                )
            if "mod_roles" not in server_config:
                return False
            if (
                role.id
                in self.config["plugin_config"][str(message.server)]["mod_roles"]
            ):
                return True
            # we could potentially check for a discord permission here instead, but
            # idk which one would appropriately fit the ability to manage the bot.
            # maybe "manage server"
        return False

    def embed(
        self,
        title=Empty,
        url=Empty,
        desc=Empty,
        author_name=Empty,
        author_link=Empty,
        author_icon=Empty,
        fields=[],
        footer=Empty,
        color="000",
        thumbnail=Empty,
        image=Empty,
    ):
        e = discord.Embed(title=title, url=url, description=desc, color=int(color, 16))
        if thumbnail:
            e.set_thumbnail(url=thumbnail)
        for field in fields:
            e.add_field(
                name=field[0],
                value=field[1],
                inline=field[2] if len(field) > 2 and not field[2] else True,
            )
        # only set author values if author name exists
        if author_name:
            e.set_author(
                name=author_name,
                url=author_link or Empty,
                icon_url=author_icon or Empty,
            )
        if footer:
            e.set_footer(text=footer)
        if image:
            e.set_image(url=image)
        return e

    def delete_message(self, channel, message, after=0):
        gaysyncio(
            [
                [asyncio.sleep, (after,), {}],
                [client.get_message, (channel, message), {}],
                [client.delete_message, ("$1",), {}],
            ]
        )

    def menu(
        self,
        target,
        user,
        question,
        answers=None,
        ync=None,
        cancel=False,
        delete_after=False,
    ):
        if ync:
            if len(ync) != 3:
                raise util.Error(
                    "ync must have 3 elements:" "a function for yes, no, and cancel"
                )
            reactions = ["👍", "👎", "❌"]
            answers = ["Yes", "No", "Cancel"]
            functions = ync
        else:
            if not answers:
                raise util.Error("You can't call this function with no answers")
            if len(answers) > 11:
                raise util.Error(
                    "A maximum of 11 options are supported. You supplied %s"
                    % len(answers)
                )
            numbers = ["1⃣", "2⃣", "3⃣", "4⃣", "5⃣", "6⃣", "7⃣", "8⃣", "9⃣", "🔟", "0⃣"]
            # if user supplies an icon to use, use that, else use a number icon
            reactions = [
                numbers[i] if len(a) < 3 else a[0] for i, a in enumerate(answers)
            ]
            # parse the answers array, ignoring the supplied icon if supplied
            answers, functions = zip(
                *[a_f if len(a_f) < 3 else a_f[1:3] for a_f in answers]
            )
        message = "%s\n\n%s\n\nReact to answer." % (
            question,
            "\n".join(["[%s] - %s" % (r, a) for r, a in zip(reactions, answers)]),
        )
        self.msg(
            target,
            message,
            reactions=zip(reactions, functions),
            user=user,
            delete_after=delete_after,
        )

    def prompt(self, target, user, prompt, handler, cancel=False, timeout=60.0):
        cancel = cancel if cancel else lambda r: None

        def cancel_wrapper(r):
            # stop listening for the next message
            del self.message_callbacks[r["channel"] + user]
            # run the user submitted cancel function if supplied
            cancel(r)

        async def f(a):
            self.message_callbacks[a.channel.id + user] = [
                time.time(),
                handler,
                timeout,
            ]

        self.msg(
            target,
            prompt,
            reactions=[["❌", cancel_wrapper]],
            callback=[f, ("$0",), {}],
            user=user,
        )

    # discord method wrappers
    def msg(
        self,
        target,
        message,
        embed=None,
        components=[],
        reactions=tuple(),
        user=None,
        callback=None,
        files=[],
        delete_after=False,
        follows=None  # the message this message is in response to. Will be tracked
        # for message updates
    ):
        if type(target) == str:
            if target.isnumeric():
                target = int(target)
        if type(target) == Int64:  # for some reason pymongo returns ints as
            target = int(target)  # int64 for no reason
        if type(target) == int:
            t = self.client.get_channel(target)
            if not t:
                print(target)
                t = self.client.get_user(target)
            target = t
        if not target:
            # target does not exist
            return False
        if type(message) == util.Message:
            message = message.content
        if message != "":
            # a list of asynchronous calls to make
            async_calls = []
            # sending the message
            async_calls.append(
                [target.send, (message,), {"embed": embed, "components": components, "files": [discord.File(f, filename=fn) for fn, f in files]}]
            )
            if follows:
                # if the message is already being followed
                if follows.raw_message.id in self.followed_messages:
                    # delete the old response before posting a new one
                    async_calls.append(
                        [
                            self.followed_messages[follows.raw_message.id].delete,
                            tuple(),
                            {},
                        ]
                    )
                    del self.followed_messages[follows.raw_message.id]
                # register that this message is a response
                async def follow_message(m):
                    self.followed_messages[follows.raw_message.id] = m

                async_calls.append([follow_message, ("$0",), {}])

            reactions = list(reactions)
            # add the reactions
            for r, f in reactions:

                async def add_reaction(message, reaction):
                    return await message.add_reaction(reaction)

                async_calls.append([add_reaction, ("$0", r), {}])
            # a callback for when the message and all the reactions have been sent
            async def add_reaction_callbacks(message):
                # make a note of the message id, so that if the user clicks them
                # the reaction callback function is run
                self.reaction_callbacks[message.id] = (user, reactions)

            # finally, add the reactions callback if required
            if reactions:
                async_calls.append([add_reaction_callbacks, ("$0",), {}])
            if callback:
                async_calls.append(callback)
            if delete_after:

                async def d(message, delay):
                    await message.delete(delay=delay)

                async_calls.append([d, ("$0", delete_after), {}])
            self.gaysyncio(async_calls)
            self.trigger("sent", target, message, embed)

    def add_reaction(self, emoji, message):
        async def add_reaction(message, reaction):
            return await message.add_reaction(reaction)

        self.gaysyncio([[add_reaction, (message.raw_message, emoji), {}]])

    def join(self, channel):
        pass

    # how to mention a target in text.
    def mention(self, target):
        if isinstance(target, str) and target.isnumeric():
            target = int(target)
        channel = self.client.get_channel(target)
        if channel:
            return "<#%s>" % target
        user = discord.utils.get(self.client.users, id=target)
        if user:
            return "<@%s>" % target
        return str(target)

    # gets the user mentions from a string
    def get_mentions(self, message):
        return [m.id for m in message.raw_message.mentions]

    def me(self):
        return self.client.user.id

    # event handler handling
    def on(self, command, plugin_name):
        def handler(f):
            # remove old handlers from this plugin
            if command in self.callbacks:
                for callback in self.callbacks[command]:
                    if callback[1] == plugin_name:
                        self.callbacks[command].remove(callback)
            self.add_callback(f, command, plugin_name)

        return handler

    # removes an even handler
    def off(self, f, command):
        if command in self.callbacks:
            self.callbacks[command].remove(f)

    def trigger(self, event, *data):
        if event != "message":
            print(event)
        if event in self.callbacks:
            for callback, plugin in self.callbacks[event]:
                if hasattr(data[0], "target"):
                    if not self.plugin_valid(plugin, data[0]):
                        continue
                callback(*data)

    def add_callback(self, callback, command, plugin_name):
        if command not in self.callbacks:
            self.callbacks[command] = []
        self.callbacks[command].append((callback, plugin_name))

    # returns true if server plugin should respond to message
    def plugin_valid(self, plugin, message):
        if not isinstance(plugin, str):
            plugin = plugin.name
        # message is a DM, and therefore cannot be blacklisted
        if not message.raw_message.guild:
            return True
        if not "plugin_config" in self.config:
            return True
        # if there's a plugin config entry for the server this
        # event is happening in
        server_id = str(message.raw_message.guild.id)
        if server_id in self.config["plugin_config"]:
            server_config = self.config["plugin_config"][server_id]
            # if the server has blacklisted channels for this plugin
            if "blacklist" in server_config and plugin in server_config["blacklist"]:
                # is this channel blacklisted?
                if server_config["blacklist"][plugin] == True:
                    # the whole server is blacklisted
                    return False
                # if this channel is in the blacklist
                if message.target in server_config["blacklist"][plugin]:
                    # this channel is blacklisted
                    return False
                # if the blacklist is just a single channel, and it's us
                if (
                    isinstance(server_config["blacklist"][plugin], int)
                    and message.target == server_config["blacklist"][plugin]
                ):
                    return False
            # what if there is a whitelist, but this channel isn't on it?
            if "whitelist" in server_config and plugin in server_config["whitelist"]:
                # just to maintain symetry with the blacklist. No reason to do this
                if server_config["whitelist"][plugin] == True:
                    return True
                # if channel not in list of whitelisted channels
                if message.target not in server_config["whitelist"][plugin]:
                    return False
                # if the whitelist is just a single channel and we're not it
                if (
                    isinstance(server_config["whitelist"][plugin], int)
                    and message.target != server_config["whitelist"][plugin]
                ):
                    return False
        return True

    def format_message(self, m):
        return util.Message(
            nick=m.author.nick if hasattr(m.author, "nick") else m.author.name,
            username="%s#%s" % (m.author.name, m.author.discriminator),
            author_id=m.author.id,
            type="message",
            target=m.channel.id,
            server=m.guild.id if m.guild else False,
            content=m.content,
            raw_message=m,
            server_type="discord",
            timestamp=m.created_at,
            embeds=m.embeds,
            components=m.components,
            attachments=m.attachments
        )

    def gaysyncio(self, calls):
        async def f():
            # make a buffer of output values
            buffer = []
            # if one of the args starts with a $, replace it with it's index inbuffer
            for function, args, kwargs in calls:
                args2 = []
                for arg in args:
                    if type(arg) == str and len(arg) > 1 and arg[0] == "$":
                        try:
                            args2.append(buffer[int(arg[1:])])
                        except IndexError:
                            args2.append(arg)
                    else:
                        args2.append(arg)
                args = args2
                buffer.append(await function(*args, **kwargs))

        self.client.loop.create_task(f())
