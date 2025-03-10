import feedparser
import pymongo
import time
import re
from tomd import Tomd
import html
from bs4 import BeautifulSoup
from taiiwobot.plugin import Plugin
from dateutil import parser


class RSS(Plugin):
    def __init__(self, bot):
        self.bot = bot
        self.interface = bot.util.Interface(
            "rss",
            "Posts RSS updates to a channel",
            [],
            self.root,
            subcommands=[
                bot.util.Interface(
                    "add",
                    "Adds an RSS feed to listen to. Usage: $rss add [flags] <url>",
                    [
                        "t target The target channel the feed updates will appear in 1",
                        'c conditions Filter keys with a regex. Eg: `-c="title=weather; desc=UK` to only post entries with "weather" in the title, and "UK" in the description 1',
                    ],
                    self.add,
                ),
                bot.util.Interface(
                    "remove",
                    "Removes an RSS feed from the target channel. Usage $rss remove [flags] <url>",
                    ["t target The target channel to delete the feeds from 1"],
                    self.delete,
                ),
                bot.util.Interface(
                    "edit",
                    "Edits the way a feed is managed. Usage $rss edit [flags] <url>",
                    [
                        "t target The channel that contains the feed to be edited 1",
                        "d formatting Show the edit formatting menu 0",
                        "ea edit-attribute The name of the embed attribute to be edited 1",
                        "c conditions Show the condition editing menu 0",
                        "cc create-condition Specify a condition to create 0",
                        "dc delete-condition Delete a condition 0",
                    ],
                    self.edit,
                ),
            ],
        ).listen()

        self.db = self.bot.util.get_db()
        self.feeds_col = self.db["rss_feeds"]
        self.default_settings = {
            "message": None,
            "title": "$title",
            "desc": "$description",
            "footer": "published at $published",
            "url": "$link",
            "thumbnail": None,
            "author_name": "$feed:title",
            "author_link": "$feed:link",
            "author_icon": None,
            "color": "0xbade83",
        }
        self.stopped = False
        self.loop_thread = self.bot.util.thread(self.loop)

    def root(self, message):
        # self.bot.msg(message.target, "%s %s %s" % (output, force, quiet))
        self.interface.help(message.target, self)

    @Plugin.authenticated
    def add(self, message, url, target="", conditions=""):
        if not url:
            raise self.bot.util.RuntimeError(
                "Missing argument: url. Usage: $rss add [flags] <url>",
                message.target,
                self,
            )
        # parse the input values
        target = target if target else message.target
        target = target if type(target) == int else int(target)
        existing_feed = self.feeds_col.find_one({"url": url})
        if url[:7] not in ["https:/", "http://"]:
            raise self.bot.util.RuntimeError(
                "`url` must be a valid URL meaning it must start with http:// "
                "or https://",
                message.target,
                self,
            )
        feed_sample = feedparser.parse(url)
        if len(feed_sample["entries"]) < 1:
            raise self.bot.util.RuntimeError(
                "This does not appear to be a valid feed. "
                "Feeds must have at least 1 entry.",
                message.target,
                self,
            )
        if existing_feed:
            for destination in existing_feed["destinations"]:
                if target == destination["target"]:
                    raise self.bot.util.RuntimeError(
                        "That feed is already being tracked in this channel! "
                        "To edit the way that feed is managed, type: `%s edit`"
                        "To remove that feed, type `%s remove <url>`"
                        % (
                            self.interface.prefix + self.interface.name,
                            self.interface.prefix + self.interface.name,
                        ),
                        message.target,
                        self,
                    )
            feed = existing_feed
        else:
            post_times = sorted(
                [parser.parse(e["updated"]) for e in feed_sample["entries"]]
            )
            feed = {
                "url": url,
                "latest_post": post_times[-1],
                "destinations": [],
            }
        destination = {
            "target": target,
            "keys": "default",
            "conditions": [conditions],
        }
        entry = feed_sample["entries"][0]
        entry.update({"feed:" + k: v for k, v in feed_sample.items()})
        # post a sample of the feed
        self.post_entry(
            {"target": message.target, "keys": "default", "conditions": [conditions],},
            entry,
        )
        # callback function for if the user hits "yes" in the following menu
        def yes(r):
            # the user decided the feed looked good
            if existing_feed:
                # edit the existing feed
                self.feeds_col.update(
                    existing_feed, {"$push": {"destinations": destination}}
                )
            else:
                # insert a new feed into the db
                feed["destinations"].append(destination)
                self.feeds_col.insert_one(feed)

        # ask the user if it needs editing
        self.bot.menu(
            message.target,
            message.author_id,
            "Does this look okay?",
            ync=[yes, lambda r: yes(r) + self.edit(message, url), lambda r: None],
        )

    @Plugin.authenticated
    def delete(self, message, *args, target=None):
        url = args[0]
        if not url:
            raise self.bot.util.RuntimeError(
                "Missing argument: url. Usage: $rss delete [flags] <url>",
                message.target,
                self,
            )
        target = target if target else message.target
        target = target if type(target) == int else int(target)
        feed = self.feeds_col.find_one({"url": url})
        # if this is the only place the feed is used
        if len(feed["destinations"]) == 1:
            # remove the whole feed
            self.feeds_col.remove(feed)
        else:
            # delete this destination
            self.feeds_col.update(feed, {"$pull": {"destination": {"target": target}}})

    @Plugin.authenticated
    def edit(
        self,
        message,
        url=None,
        target=None,
        formatting=False,
        edit_attribute=None,
        conditions=False,
        create_condition=False,
        edit_condition=None,
        delete_condition=None,
    ):
        # sanitize user input
        target = target if target else message.target
        target = target if type(target) == int else int(target)
        if not url:
            urls = self.feeds_col.find({"destinations.target": target}, {"url": True})
            # array of arrays: answer, function
            def lambda_factory(u):
                return lambda r: self.edit(
                    message, u["url"], target, formatting, edit_attribute, conditions
                )

            answers = [
                [u["url"], lambda_factory(u)]
                for u in self.feeds_col.find({"destinations.target": target})
            ]
            self.bot.menu(
                message.target,
                message.author_id,
                "Which feed would you like to edit?",
                answers,
            )
            return
        # get the feed
        feed = self.feeds_col.find_one({"url": url})
        if not feed:
            raise self.bot.util.RuntimeError(
                "That feed is not currently being tracked. If you meant to add"
                "it use $rss add <url>",
                message.target,
                self,
            )
        # get the destination
        destination = [f for f in feed["destinations"] if f["target"] == target][0]
        # which menu is being requested
        if formatting:
            keys = (
                self.default_settings
                if destination["keys"] == "default"
                else destination["keys"]
            )
            # array of arrays: answer, function
            def lambda_factory(a):
                return lambda r: self.edit(message, url, target, edit_attribute=a)

            answers = [
                ["[%s] = %s\n" % (a, b), lambda_factory(a)] for a, b in keys.items()
            ]
            self.bot.menu(
                message.target,
                message.author_id,
                "Which embed attribute would you like to change?",
                answers,
            )
        elif edit_attribute:
            # build a set of example entry variables to choose from
            sample_feed = feedparser.parse(feed["url"])
            sample_entry = sample_feed["entries"][0]
            # remove redundant data
            del sample_feed["entries"]
            sample_entry.update({"feed:" + k: v for k, v in sample_feed.items()})
            keys = (
                self.default_settings
                if destination["keys"] == "default"
                else destination["keys"]
            )
            msg = (
                "Here's a sample of the data each feed entry has available:\n\n"
                "```\n%s\n```"
                % "\n".join(
                    [
                        "$%s - %s" % (k, v[:100])
                        for k, v in sample_entry.items()
                        if type(v) == str
                    ]
                )
            )
            self.bot.msg(message.target, msg)

            def confirm(m):
                value = m.content
                d = destination.copy()
                if d["keys"] == "default":
                    d["keys"] = self.default_settings
                d["keys"][edit_attribute] = value
                sample_d = d.copy()
                sample_d["target"] = message.target
                # post an example for the user to validate
                self.post_entry(sample_d, sample_entry)
                # if the users says yes
                def yes(r):
                    # write the new destination to the database
                    self.feeds_col.update(
                        {"url": url, "destinations.target": target},
                        {"$set": {"destinations.$": d}},
                    )
                    # print complete message
                    self.bot.msg(message.target, "Changed have been made.")

                # ask the user to validate the new formatting
                self.bot.menu(
                    message.target,
                    message.author_id,
                    "Does this look okay?",
                    ync=[
                        yes,
                        lambda r: self.edit(
                            message, url, target, edit_attribute=edit_attribute
                        ),
                        lambda r: None,
                    ],
                )

            # prompt the user to type the new attribute contents
            self.bot.prompt(
                message.target,
                message.author_id,
                "Type the way you want the %s of the entry to be displayed:"
                % edit_attribute,
                confirm,
            )
        elif conditions:
            # prepend a "create condition" option to the start of the list
            self.bot.menu(
                message.target,
                message.author_id,
                "How would you like to edit the conditions?",
                [
                    [
                        "Create a new condition",
                        lambda r: self.edit(
                            message, url, target, create_condition=True
                        ),
                    ],
                    [
                        "Delete an existing condition",
                        lambda r: self.edit(
                            message, url, target, delete_condition=True
                        ),
                    ],
                ],
            )
        elif create_condition:
            # build a set of example entry variables to choose from
            sample_entry = feedparser.parse(feed["url"])["entries"][0]
            msg = (
                "Here's a sample of the data each feed entry has available:\n\n"
                "```\n%s\n```"
                % "\n".join(
                    [
                        "%s - %s" % (k, v[:100])
                        for k, v in sample_entry.items()
                        if type(v) == str
                    ]
                )
            )
            self.bot.msg(message.target, msg)

            def append_condition(condition):
                print(condition)
                condict = self.parse_condition(condition)
                self.feeds_col.update(
                    {"url": url, "destinations.target": target},
                    {"$push": {"destinations.$.conditions": condition}},
                )
                self.bot.msg(
                    message.target, "Your condition has been added to the feed."
                )

            # prompt for a condition
            self.bot.prompt(
                message.target,
                message.author,
                "Enter your desired condition matching the format of the "
                + "example: `title=(720p|1080p); title=Joker, where "
                + "'title' would contain'720p' or '1080p', AND contain 'Joker'",
                lambda m: append_condition(m.content),
            )
        elif delete_condition:

            def delete_condition_f(condition):
                r = self.feeds_col.update(
                    {"url": url, "destinations.target": target},
                    {"$pull": {"destinations.$.conditions": condition}},
                )
                self.bot.msg(message.target, "Condition deleted.")

            # ask them which condition they want to delete
            for destination in feed["destinations"]:
                if destination["target"] == target:
                    conditions = destination["conditions"]
            self.bot.menu(
                message.target,
                message.author,
                "Select the condition you'd like to delete: ",
                [[c, lambda r: delete_condition_f(c)] for c in conditions],
            )

        else:
            # ask which property to be edited
            self.bot.menu(
                message.target,
                message.author_id,
                "What aspect of this feed would you like to edit?",
                [
                    [
                        "Format - The way the feed is formated",
                        lambda r: self.edit(message, url, target, formatting=True),
                    ],
                    [
                        "Conditions - Which entries are posted",
                        lambda r: self.edit(message, url, target, conditions=True),
                    ],
                ],
            )

    # parses a post condition string
    def parse_condition(self, condition):
        if condition == "":
            return None
        # split by any ;<space>
        conditions = re.split(r"[^\\];\s?", condition)
        ret = {}
        for c in conditions:
            # Basically splits by =, allowing for \=
            m = re.match(r"^(.+[^\\])=(.*)", c)
            key = m.group(1).replace(r"\=", "=")
            ret[key] = re.split(r",\s?", m.group(2))
        return ret

    def post_entry(self, destination, entry):
        keys = (
            self.default_settings
            if destination["keys"] == "default"
            else destination["keys"]
        )
        # find any images from within the summary and add them to the embed
        soup = BeautifulSoup(entry["summary"], "html.parser")
        imgs = soup.find_all("img")
        image = None
        for img in imgs:
            if not image:
                image = img["src"]
            img.decompose()
        entry["summary"] = re.sub(r"<br ?/?>", "\n", str(soup))
        entry["summary"] = re.sub(r"<!--.*-->", "", entry["summary"])
        # remove html formatting from the description
        print(entry["summary"])
        summary = html.unescape(
            Tomd(
                "<p>"
                + entry["summary"].replace("%22", '"').replace("%3E", ">")
                + "</p>"
            ).markdown.strip()
        )
        entry["summary"] = summary if summary != "" else entry["summary"]
        entry["description"] = entry["summary"]  # alias
        # turns a key format into value string
        def format_key(t):
            v = []
            # for each word in the value of the key if value exists
            for k in keys[t].split() if t in keys and keys[t] else []:
                if k[0] == "$":
                    v.append(entry[k[1:]] if k[1:] in entry else None)
                else:
                    v.append(k)
            # if we're missing any of the values, display nothing
            return " ".join(v) if not None in v else None

        return self.bot.msg(
            destination["target"],
            format_key("message") + " ",
            embed=self.bot.server.embed(
                title=format_key("title"),
                desc=format_key("desc")[:2047],
                author_name=format_key("author_name"),
                author_link=format_key("author_link"),
                author_icon=format_key("author_icon"),
                footer=format_key("footer"),
                url=format_key("url"),
                color=format_key("color"),
                image=image,
            ),
        )

    def loop(self):
        while not self.stopped:
            print("checking for feeds")
            for feed in self.feeds_col.find({}):
                f = feedparser.parse(feed["url"])
                if "entries" not in f:
                    # could not get feed. Disconnected from the internet?
                    continue
                entries = []
                latest_post = feed["latest_post"]
                for entry in f["entries"]:
                    if (
                        parser.parse(entry["updated"], ignoretz=True)
                        > feed["latest_post"]
                    ):
                        entries.append(entry)
                        if parser.parse(entry["updated"], ignoretz=True) > latest_post:
                            latest_post = parser.parse(entry["updated"], ignoretz=True)
                # if there are no new entries
                if len(entries) == 0:
                    # no new articles, go to next feed
                    continue
                # remove entry list from the feed to save resources
                del f["entries"]
                # for each new entry
                for entry in entries:
                    # add some of the feed keys for use in markup
                    entry.update({"feed:" + k: v for k, v in f.items()})
                    # print(entry["title"])
                    for destination in feed["destinations"]:
                        # run the conditions against the entry
                        if destination["conditions"]:
                            for conditions in destination["conditions"]:
                                match = False
                                condict = self.parse_condition(conditions)
                                if not condict:
                                    # conditions were invalid, set match to true
                                    # but also evaluate the other conditions
                                    match = True
                                    continue
                                for key, condition in condict.items():
                                    for cond in condition:
                                        if key == "" or re.search(cond, entry[key]):
                                            match = True
                                            break
                        else:
                            # default to true if there are no conditions
                            match = True
                        if not match:
                            # entry does not match the conditions for this dest
                            continue
                        self.post_entry(destination, entry)
                self.feeds_col.update(feed, {"$set": {"latest_post": latest_post}})
            time.sleep(60 * 10)

    def unload(self):
        super().unload()
        self.stopped = True
