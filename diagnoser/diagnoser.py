from __future__ import annotations

import itertools
from copy import copy
from dataclasses import dataclass
from functools import partial
from typing import Awaitable, Callable, Iterable, List, Optional, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import bold, format_perms_list, humanize_list, inline

_ = lambda s: s


@dataclass
class CheckResult:
    success: bool
    label: str
    details: Union[List[CheckResult], str] = ""
    resolution: str = ""


class IssueDiagnoserBase:
    def __init__(
        self,
        bot: Red,
        original_ctx: commands.Context,
        channel: discord.TextChannel,
        author: discord.Member,
        command: commands.Command,
    ) -> None:
        self.bot = bot
        self._original_ctx = original_ctx
        self.guild = channel.guild
        self.channel = channel
        self.author = author
        self.command = command
        self._prepared = False
        self.message: discord.Message
        self.ctx: commands.Context

    async def _prepare(self) -> None:
        if self._prepared:
            return
        self.message = copy(self._original_ctx.message)
        self.message.author = self.author
        self.message.channel = self.channel
        self.message.content = self._original_ctx.prefix + self.command.qualified_name

        self.ctx = await self.bot.get_context(self.message)

    # reusable methods
    async def _check_until_fail(
        self,
        label: str,
        checks: Iterable[Callable[[], Awaitable[CheckResult]]],
        *,
        final_check_result: Optional[CheckResult] = None,
    ) -> CheckResult:
        details = []
        for check in checks:
            check_result = await check()
            details.append(check_result)
            if not check_result.success:
                return CheckResult(False, label, details, check_result.resolution)
        if final_check_result is not None:
            details.append(final_check_result)
            return CheckResult(
                final_check_result.success,
                label,
                details,
                final_check_result.resolution,
            )
        return CheckResult(True, label, details)

    def _format_command_name(self, command: Union[commands.Command, str]) -> str:
        if not isinstance(command, str):
            command = command.qualified_name
        return inline(f"{self._original_ctx.clean_prefix}{command}")


class DetailedGlobalCallOnceChecksMixin(IssueDiagnoserBase):
    async def _check_is_author_bot(self) -> CheckResult:
        label = _("Check if the command caller is not a bot")
        if not self.author.bot:
            return CheckResult(True, label)
        return CheckResult(
            False,
            label,
            _("The user is a bot which prevents them from running any command."),
            _("This cannot be fixed - bots should not be listening to other bots."),
        )

    async def _check_can_bot_send_messages(self) -> CheckResult:
        label = _("Check if the bot can send messages in the given channel")
        if self.channel.permissions_for(self.guild.me).send_messages:
            return CheckResult(True, label)
        return CheckResult(
            False,
            label,
            _("Bot doesn't have permission to send messages in the given channel."),
            _(
                "To fix this issue, ensure that the permissions setup allows the bot"
                " to send messages per Discord's role hierarchy:\n"
                "https://support.discord.com/hc/en-us/articles/206141927"
            ),
        )

    # While the following 2 checks could show even more precise error message,
    # it would require a usage of private attribute rather than the public API
    # which increases maintanance burden for not that big of benefit.
    async def _check_ignored_issues(self) -> CheckResult:
        label = _("Check if the channel and the server aren't set to be ignored")
        if await self.bot.ignored_channel_or_guild(self.message):
            return CheckResult(True, label)

        if self.channel.category is None:
            resolution = _(
                "To fix this issue, check the list returned by the {command} command"
                " and ensure that the {channel} channel and the server aren't a part of that list."
            ).format(
                command=self._format_command_name("ignore list"),
                channel=self.channel.mention,
            )
        else:
            resolution = _(
                "To fix this issue, check the list returned by the {command} command"
                " and ensure that the {channel} channel,"
                " the channel category it belongs to ({channel_category}),"
                " and the server aren't a part of that list."
            ).format(
                command=self._format_command_name("ignore list"),
                channel=self.channel.mention,
                channel_category=self.channel.category.mention,
            )

        return CheckResult(
            False,
            label,
            _("The bot is set to ignore commands in the given channel or this server."),
            resolution,
        )

    async def _get_detailed_global_whitelist_blacklist_result(self, label: str) -> CheckResult:
        global_whitelist = await self.bot._whiteblacklist_cache.get_whitelist()
        if global_whitelist:
            return CheckResult(
                False,
                label,
                _("Global allowlist prevents the user from running this command."),
                _(
                    "To fix this issue, you can either add the user to the allowlist,"
                    " or clear the allowlist.\n"
                    "If you want to keep the allowlist, you can run {command_1} which will"
                    " add {user} to the allowlist.\n"
                    "If you instead want to clear the allowlist and let all users"
                    " run commands freely, you can run {command_2} to do that."
                ).format(
                    command_1=self._format_command_name(f"allowlist add {self.author.id}"),
                    user=self.author,
                    command_2=self._format_command_name("allowlist clear"),
                ),
            )
        return CheckResult(
            False,
            label,
            _("Global blocklist prevents the user from running this command."),
            _(
                "To fix this issue, you can either remove the user from the blocklist,"
                " or clear the blocklist.\n"
                "If you want to keep the blocklist, you can run {command_1} which will"
                " remove {user} from the blocklist.\n"
                "If you instead want to clear the blocklist and let all users"
                " run commands freely, you can run {command_2} to do that."
            ).format(
                command_1=self._format_command_name(f"blocklist remove {self.author.id}"),
                user=self.author,
                command_2=self._format_command_name("blocklist clear"),
            ),
        )

    async def _get_detailed_local_whitelist_blacklist_result(self, label: str) -> CheckResult:
        # this method skips guild owner check as the earlier checks wouldn't fail
        # if the user were guild owner
        guild_whitelist = await self.bot._whiteblacklist_cache.get_whitelist(self.guild)
        if guild_whitelist:
            return CheckResult(
                False,
                label,
                _("Local allowlist prevents the user from running this command."),
                _(
                    "To fix this issue, you can either add the user or one of their roles"
                    " to the local allowlist, or clear the local allowlist.\n"
                    "If you want to keep the local allowlist, you can run {command_1} which will"
                    " add {user} to the local allowlist.\n"
                    "If you instead want to clear the local allowlist and let all users"
                    " run commands freely, you can run {command_2} to do that."
                ).format(
                    command_1=self._format_command_name(f"localallowlist add {self.author.id}"),
                    user=self.author,
                    command_2=self._format_command_name("localallowlist clear"),
                ),
            )

        details = _("Local blocklist prevents the user from running this command.")
        guild_blacklist = await self.bot._whiteblacklist_cache.get_blacklist(self.guild)
        ids = {role.id for role in self.author.roles if not role.is_default()}
        ids.add(self.author.id)
        intersection = ids & guild_blacklist
        try:
            intersection.remove(self.author.id)
        except KeyError:
            # author is not part of the blocklist
            to_remove = list(intersection)
            role_names = [self.guild.get_role(role_id).name for role_id in to_remove]
            return CheckResult(
                False,
                label,
                details,
                _(
                    "To fix this issue, you can either remove the user's roles"
                    " from the local blocklist, or clear the local blocklist.\n"
                    "If you want to keep the local blocklist, you can run {command_1} which will"
                    " remove the user's roles ({roles}) from the local blocklist.\n"
                    "If you instead want to clear the local blocklist and let all users"
                    " run commands freely, you can run {command_2} to do that."
                ).format(
                    command_1=self._format_command_name(
                        f"localblocklist remove {' '.join(map(str, to_remove))}"
                    ),
                    roles=humanize_list(role_names),
                    command_2=self._format_command_name("localblocklist clear"),
                ),
            )

        if intersection:
            # both author and some of their roles are part of the blocklist
            to_remove = list(intersection)
            role_names = [self.guild.get_role(role_id).name for role_id in to_remove]
            to_remove.append(self.author.id)
            return CheckResult(
                False,
                label,
                details,
                _(
                    "To fix this issue, you can either remove the user and their roles"
                    " from the local blocklist, or clear the local blocklist.\n"
                    "If you want to keep the local blocklist, you can run {command_1} which will"
                    " remove {user} and their roles ({roles}) from the local blocklist.\n"
                    "If you instead want to clear the local blocklist and let all users"
                    " run commands freely, you can run {command_2} to do that."
                ).format(
                    command_1=self._format_command_name(
                        f"localblocklist remove {' '.join(map(str, to_remove))}"
                    ),
                    user=self.author,
                    roles=humanize_list(role_names),
                    command_2=self._format_command_name("localblocklist clear"),
                ),
            )

        # only the author is part of the blocklist
        return CheckResult(
            False,
            label,
            details,
            _(
                "To fix this issue, you can either remove the user"
                " from the local blocklist, or clear the local blocklist.\n"
                " If you want to keep the local blocklist, you can run {command_1} which will"
                " remove {user} from the local blocklist.\n"
                "If you instead want to clear the local blocklist and let all users"
                " run commands freely, you can run {command_2} to do that."
            ).format(
                command_1=self._format_command_name(f"localblocklist remove {self.author.id}"),
                user=self.author,
                command_2=self._format_command_name("localblocklist clear"),
            ),
        )

    async def _check_whitelist_blacklist_issues(self) -> CheckResult:
        label = _("Allowlist and blocklist checks")
        if await self.bot.allowed_by_whitelist_blacklist(self.author):
            return CheckResult(True, label)

        is_global = not await self.bot.allowed_by_whitelist_blacklist(who_id=self.author.id)
        if is_global:
            return await self._get_detailed_global_whitelist_blacklist_result(label)

        return await self._get_detailed_local_whitelist_blacklist_result(label)


class DetailedCommandChecksMixin(IssueDiagnoserBase):
    def _command_error_handler(
        self,
        msg: str,
        label: str,
        failed_with_message: str,
        failed_without_message: str,
    ) -> CheckResult:
        command = self.ctx.command
        details = (
            failed_with_message.format(command=self._format_command_name(command), message=msg)
            if msg
            else failed_without_message.format(command=self._format_command_name(command))
        )
        return CheckResult(
            False,
            label,
            details,
        )

    async def _check_dpy_can_run(self) -> CheckResult:
        label = _("Global, cog and command checks")
        command = self.ctx.command
        try:
            if await super(commands.Command, command).can_run(self.ctx):
                return CheckResult(True, label)
        except commands.DisabledCommand:
            details = (
                _("The given command is disabled in this guild.")
                if command is self.command
                else _("One of the parents of the given command is disabled globally.")
            )
            return CheckResult(
                False,
                label,
                details,
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_command} command in this guild."
                ).format(
                    command=self._format_command_name(f"command enable guild {command}"),
                    affected_command=self._format_command_name(command),
                ),
            )
        except commands.CommandError:
            # we want to narrow this down to specific type of checks (bot/cog/command)
            pass

        return await self._check_until_fail(
            label,
            (
                self._check_dpy_can_run_bot,
                self._check_dpy_can_run_cog,
                self._check_dpy_can_run_command,
            ),
            final_check_result=CheckResult(
                False,
                _("Other issues related to the checks"),
                _(
                    "There's an issue related to the checks for {command}"
                    " but we're not able to determine the exact cause."
                ),
                _(
                    "To fix this issue, a manual review of"
                    " the global, cog and command checks is required."
                ),
            ),
        )

    async def _check_dpy_can_run_bot(self) -> CheckResult:
        label = _("Global checks")
        msg = ""
        try:
            if await self.bot.can_run(self.ctx):
                return CheckResult(True, label)
        except commands.CommandError as e:
            msg = str(e)
        return self._command_error_handler(
            msg,
            label,
            _(
                "One of the global checks for the command {command} failed with a message:\n"
                "{message}"
            ),
            _("One of the global checks for the command {command} failed without a message."),
        )

    async def _check_dpy_can_run_cog(self) -> CheckResult:
        label = _("Cog check")
        cog = self.ctx.command.cog
        if cog is None:
            return CheckResult(True, label)
        local_check = commands.Cog._get_overridden_method(cog.cog_check)
        if local_check is None:
            return CheckResult(True, label)

        msg = ""
        try:
            if await discord.utils.maybe_coroutine(local_check, self.ctx):
                return CheckResult(True, label)
        except commands.CommandError as e:
            msg = str(e)
        return self._command_error_handler(
            msg,
            label,
            _("The cog check for the command {command} failed with a message:\n{message}"),
            _("The cog check for the command {command} failed without a message."),
        )

    async def _check_dpy_can_run_command(self) -> CheckResult:
        label = _("Command checks")
        predicates = self.ctx.command.checks
        if not predicates:
            return CheckResult(True, label)

        msg = ""
        try:
            if await discord.utils.async_all(predicate(self.ctx) for predicate in predicates):
                return CheckResult(True, label)
        except commands.CommandError as e:
            msg = str(e)
        return self._command_error_handler(
            msg,
            label,
            _(
                "One of the command checks for the command {command} failed with a message:\n"
                "{message}"
            ),
            _("One of the command checks for the command {command} failed without a message."),
        )

    async def _check_requires_command(self) -> CheckResult:
        return await self._check_requires(_("Permissions verification"), self.ctx.command)

    async def _check_requires_cog(self) -> CheckResult:
        label = _("Permissions verification for {cog} cog").format(
            cog=inline(self.ctx.cog.qualified_name)
        )
        if self.ctx.cog is None:
            return CheckResult(True, label)
        return await self._check_requires(label, self.ctx.cog)

    async def _check_requires(
        self, label: str, cog_or_command: commands.CogCommandMixin
    ) -> CheckResult:
        original_perm_state = self.ctx.permission_state
        try:
            allowed = await cog_or_command.requires.verify(self.ctx)
        except commands.DisabledCommand:
            return CheckResult(
                False,
                label,
                _("The cog of the given command is disabled in this guild."),
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_cog} cog in this guild."
                ).format(
                    command=self._format_command_name(
                        f"command enablecog {self.ctx.cog.qualified_name}"
                    ),
                    affected_cog=inline(self.ctx.cog.qualified_name),
                ),
            )
        except commands.BotMissingPermissions as e:
            # No, go away, "some" can refer to a single permission so plurals are just fine here!
            # Seriously. They are. Don't even question it.
            details = (
                _(
                    "Bot is missing some of the channel permissions ({permissions})"
                    " required by the {cog} cog."
                ).format(
                    permissions=format_perms_list(e.missing),
                    cog=inline(cog_or_command.qualified_name),
                )
                if cog_or_command is self.ctx.cog
                else _(
                    "Bot is missing some of the channel permissions ({permissions})"
                    " required by the {command} command."
                ).format(
                    permissions=format_perms_list(e.missing),
                    command=self._format_command_name(cog_or_command),
                )
            )
            return CheckResult(
                False,
                label,
                details,
                _(
                    "To fix this issue, grant the required permissions to the bot"
                    " through role settings or channel overrides."
                ),
            )
        if allowed:
            return CheckResult(True, label)

        self.ctx.permission_state = original_perm_state
        return await self._check_until_fail(
            label,
            (
                partial(self._check_requires_bot_owner, cog_or_command),
                partial(self._check_requires_permission_hooks, cog_or_command),
            ),
            # TODO: Split the `final_check_result` into parts to be ran by this function
            final_check_result=CheckResult(
                False,
                _("User's discord permissions, privilege level and rules from Permissions cog"),
                _("One of the above is the issue."),
                _(
                    "To fix this issue, verify each of these"
                    " and determine which part is the issue."
                ),
            ),
        )

    async def _check_requires_bot_owner(
        self, cog_or_command: commands.CogCommandMixin
    ) -> CheckResult:
        label = _("Ensure that the command is not bot owner only")
        if cog_or_command.requires.privilege_level is not commands.PrivilegeLevel.BOT_OWNER:
            return CheckResult(True, label)
        # we don't need to check whether the user is bot owner
        # as call to `verify()` would already succeed if that were the case
        return CheckResult(
            False,
            label,
            _("The command is bot owner only and the given user is not a bot owner."),
            _("This cannot be fixed - regular users cannot run bot owner only commands."),
        )

    async def _check_requires_permission_hooks(
        self, cog_or_command: commands.CogCommandMixin
    ) -> CheckResult:
        label = _("Permission hooks")
        result = await self.bot.verify_permissions_hooks(self.ctx)
        if result is None:
            return CheckResult(True, label)
        if result is True:
            # this situation is abnormal as in this situation,
            # call to `verify()` would already succeed and we wouldn't get to this point
            return CheckResult(
                False,
                label,
                _("Fatal error: the result of permission hooks is inconsistent."),
                _("To fix this issue, a manual review of the installed cogs is required."),
            )
        return CheckResult(
            False,
            label,
            _("The access has been denied by one of the bot's permissions hooks."),
            _("To fix this issue, a manual review of the installed cogs is required."),
        )

    async def _check_dpy_checks_and_requires(self, command: commands.Command) -> CheckResult:
        label = _("Checks and permissions verification for the command {command}").format(
            command=self._format_command_name(command)
        )

        self.ctx.command = command
        original_perm_state = self.ctx.permission_state
        try:
            can_run = await command.can_run(self.ctx, change_permission_state=True)
        except commands.CommandError:
            can_run = False

        if can_run:
            return CheckResult(True, label)

        self.ctx.permission_state = original_perm_state
        return await self._check_until_fail(
            label,
            (
                self._check_dpy_can_run,
                self._check_requires_command,
            ),
            final_check_result=CheckResult(
                False,
                _("Other command checks"),
                _("The given command is failing one of the required checks."),
                _("To fix this issue, a manual review of the command's checks is required."),
            ),
        )


class RootDiagnosersMixin(
    DetailedGlobalCallOnceChecksMixin,
    DetailedCommandChecksMixin,
    IssueDiagnoserBase,
):
    async def _check_global_call_once_checks_issues(self) -> CheckResult:
        label = _("Global 'call once' checks")
        # To avoid running core's global checks twice, we just run them all regularly
        # and if it turns out that invokation would end here, we go back and check each of
        # core's global check individually to give more precise error message.
        try:
            can_run = await self.bot.can_run(self.ctx, call_once=True)
        except commands.CommandError:
            pass
        else:
            if can_run:
                return CheckResult(True, label)

        return await self._check_until_fail(
            label,
            (
                self._check_is_author_bot,
                self._check_can_bot_send_messages,
                self._check_ignored_issues,
                self._check_whitelist_blacklist_issues,
            ),
            final_check_result=CheckResult(
                False,
                _("Other global 'call once' checks"),
                _(
                    "One of the global 'call once' checks implemented by a 3rd-party cog"
                    " prevents this command from being ran."
                ),
                _("To fix this issue, a manual review of the installed cogs is required."),
            ),
        )

    async def _check_disabled_command_issues(self) -> CheckResult:
        label = _("Check if the command is disabled")
        command = self.command

        for parent in reversed(command.parents):
            if parent.enabled:
                continue
            return CheckResult(
                False,
                label,
                _("One of the parents of the given command is disabled globally."),
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_command} command globally."
                ).format(
                    command=self._format_command_name(f"command enable global {parent}"),
                    affected_command=self._format_command_name(parent),
                ),
            )

        if not command.enabled:
            return CheckResult(
                False,
                label,
                _("The given command is disabled globally."),
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_command} command globally."
                ).format(
                    command=self._format_command_name(f"command enable global {command}"),
                    affected_command=self._format_command_name(command),
                ),
            )

        return CheckResult(True, label)

    async def _check_can_run_issues(self) -> CheckResult:
        label = _("Checks and permissions verification")
        ctx = self.ctx
        try:
            can_run = await self.command.can_run(ctx, check_all_parents=True)
        except commands.CommandError:
            # we want to get more specific error by narrowing down the scope,
            # so we just ignore handling this here
            #
            # NOTE: it might be worth storing this information in case we get to
            # `final_check_result`, although that's not very likely
            # Similar exception handlers further down the line could do that
            # as well if I'm gonna implement it here.
            pass
        else:
            if can_run:
                return CheckResult(True, label)

        ctx.permission_state = commands.PermState.NORMAL
        ctx.command = self.command.root_parent or self.command

        # slight discrepancy here - we're doing cog-level verify before top-level can_run
        return await self._check_until_fail(
            label,
            itertools.chain(
                (self._check_requires_cog,),
                (
                    partial(self._check_dpy_checks_and_requires, command)
                    for command in itertools.chain(reversed(self.command.parents), (self.command,))
                ),
            ),
            final_check_result=CheckResult(
                False,
                _("Other command checks"),
                _("The given command is failing one of the required checks."),
                _("To fix this issue, a manual review of the command's checks is required."),
            ),
        )


class IssueDiagnoser(RootDiagnosersMixin, IssueDiagnoserBase):
    def _get_message_from_check_result(
        self, result: CheckResult, *, prefix: str = ""
    ) -> List[str]:
        lines = []
        if not result.details:
            return []
        if isinstance(result.details, str):
            return [result.details]

        for idx, subresult in enumerate(result.details, start=1):
            status = (
                "Passed \N{WHITE HEAVY CHECK MARK}"
                if subresult.success
                else "Failed \N{NO ENTRY}\N{VARIATION SELECTOR-16}"
            )
            lines.append(f"{prefix}{idx}. {subresult.label}: {status}")
            lines.extend(self._get_message_from_check_result(subresult, prefix=f"{prefix}{idx}."))
        return lines

    async def diagnose(self) -> str:
        await self._prepare()
        lines = [
            bold(
                _(
                    "Diagnose results for issues of {user}"
                    " when trying to run {command_name} command in {channel} channel:\n"
                ).format(
                    user=self.author,
                    command_name=inline(
                        # needs to be _original_ctx - when bot is passed as the author,
                        # Context parser won't go far enough to parse for prefix
                        f"{self._original_ctx.clean_prefix}{self.command.qualified_name}"
                    ),
                    channel=self.channel.mention,
                ),
                # not perfect, but will do...
                escape_formatting=False,
            )
        ]
        result = await self._check_until_fail(
            "",
            (
                self._check_global_call_once_checks_issues,
                self._check_disabled_command_issues,
                self._check_can_run_issues,
            ),
        )
        lines.extend(self._get_message_from_check_result(result))
        lines.append("")
        if result.success:
            lines.append(
                _(
                    "All checks passed and no issues were detected."
                    " Make sure that the given parameters correspond to"
                    " the channel, user, and command name that have been problematic.\n\n"
                    "If you still can't find the issue, it is likely that one of the 3rd-party cogs"
                    " you're using adds a global or cog local before invoke hook that prevents"
                    " the command from getting invoked as this can't be diagnosed with this tool."
                )
            )
        else:
            lines.append("No further checks have been ran.\n")
            if result.resolution:
                lines.append(
                    _("The bot has been able to identify the issue.") + f" {result.resolution}"
                )
            else:
                lines.append(
                    _(
                        "The bot has been able to identify the issue."
                        " Read the details above for more information."
                    )
                )

        return "\n".join(lines)


class Diagnoser(commands.Cog):
    """Diagnose issues with command checks with ease!"""

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = bot

    # You may ask why this command is owner-only,
    # cause after all it could be quite useful to guild owners!
    # Truth to be told, that would require me to make some part of this
    # more end-user friendly rather than just bot owner friendly - terms like
    # 'global call once checks' are not of any use to someone who isn't bot owner.
    @commands.is_owner()
    @commands.command()
    async def diagnoseissues(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        member: discord.Member,
        *,
        command_name: str,
    ) -> None:
        """Diagnose issues with command checks with ease!"""
        command = self.bot.get_command(command_name)
        if command is None:
            await ctx.send("Command not found!")
            return
        if not channel.permissions_for(member).send_messages:
            # Let's make Flame happy here
            await ctx.send(
                _(
                    "Don't try to fool me, the given member can't access the {channel} channel."
                ).format(channel=channel.mention)
            )
            return
        issue_diagnoser = IssueDiagnoser(self.bot, ctx, channel, member, command)
        await ctx.send(await issue_diagnoser.diagnose())
