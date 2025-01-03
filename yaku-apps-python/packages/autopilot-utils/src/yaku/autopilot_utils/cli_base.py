# SPDX-FileCopyrightText: 2024 grow platform GmbH
#
# SPDX-License-Identifier: MIT

"""
A utility module for writing Python apps.

Python App Template
===================

This module provides some utility functions for easily creating
Python apps without having to write a lot of boilerplate code
for command line argument parsing, logging setup, result handling, etc.

There are example applications available in the `tests/app_* <https://github.com/B-S-F/yaku/tree/main/yaku-apps-python/packages/autopilot-utils/tests>`_ folders.

Simple App
----------

A very simple (fetcher) app looks like this::

    # Module yaku.app_single_command.cli
    import click
    from yaku.autopilot_utils.cli_base import make_autopilot_app, read_version_from_package
    from loguru import logger


    class CLI:
        click_name = "app_single_command"
        click_help_text = "Simple demo program for test purposes with just a simple command."

        click_setup = [
            click.option("--fail", is_flag=True),
        ]

        @staticmethod
        def click_command(fail: bool):
            logger.info("Inside click_command")
            if fail:
                raise Exception("Failing as requested...")
            logger.info("Should be doing something useful here!")


    cli = make_autopilot_app(
        provider=CLI,
        version_callback=read_version_from_package(__package__)
    )

    if __name__ == "__main__":
        cli()

The :file:`BUILD` file looks like this::

    pex_binary(
        name="app_single_command",
        entry_point="yaku.app_single_command.cli",  # the name of the module above
    )

All the setup of the app happens in the :py:func:`make_autopilot_app` function.
It requires two arguments:

* a `provider` which can be a class or a module.
* a `package_with_version_file`, which must be a package name inside which a
  :file:`_version.txt` file is located which contains the app version number.

The `provider` class (could also be a module) usually has three attributes:

* A `click_name` string which contains the app name (should be the same as the
  name of the pex binary in the :file:`BUILD` file).
* A `click_setup` (which can be omitted or an empty list) which contains a list
  of `click.option` or `click.argument` decorators (but omit the `@`). Use those
  decorators to add arguments or options to your app.
* A `click_command` function (which can be missing if there are subcommands)
  which must accept the arguments according to the `click_setup` list and which
  contains the app logic.

For more complex use-cases, other attributes are also possible:

* `click_evaluator_callback` needs to be defined if results are collected
  during the execution of the `click_command` function.
* `click_subcommands` can be used to define subcommands for a main command,
  e.g. when having an Excel evaluator which can be called like
  :command:`excel-evaluate cell --location=A1 --equals="yes"` or like
  :command:`excel-evaluate column --column-index=F --values="yes|no"`.


Click argument validation
-------------------------

When using the validation `callback=...` argument for `click.option`, you need
to adhere to the way how click is handling exceptions.

Instead of raising
:py:exc:`~yaku.autopilot_utils.errors.AutopilotConfigurationError` or similar
exceptions from :py:mod:`yaku.autopilot_utils.errors`, use `click.BadParameter`
or `click.UsageError` instead.

For details, see :py:class:`ClickUsageErrorHandlerDecorator` below.


Complex apps/evaluators
-----------------------

There are multiple app configurations possible:

* Simple app with a single command, not evaluation (see
  `app_single_command/ <https://github.com/B-S-F/yaku/tree/main/yaku-apps-python/packages/autopilot-utils/tests/app_single_command/>`_).
* Simple evaluator, called from a single command (see
  `app_single_evaluator/ <https://github.com/B-S-F/yaku/tree/main/yaku-apps-python/packages/autopilot-utils/tests/app_single_evaluator/>`_).
* Complex app with multiple subcommands, but no evaluation (see
  `app_multi_command/ <https://github.com/B-S-F/yaku/tree/main/yaku-apps-python/packages/autopilot-utils/tests/app_multi_command/>`_).
* Complex evaluator with multiple independent evaluators which can not be
  chained (see
  `app_multi_evaluator/ <https://github.com/B-S-F/yaku/tree/main/yaku-apps-python/packages/autopilot-utils/tests/app_multi_evaluator/>`_).
  This means that you can only call one of the subcommands at a time.
  This also means that each evaluator needs to compute its own evaluation result
  (that's why the subcommand providers in this example have all a custom
  `click_evaluator_callback` function)
* Complex evaluator with chainable subcommand evaluators. (see
  `app_chained_multi_evaluator/ <https://github.com/B-S-F/yaku/tree/main/yaku-apps-python/packages/autopilot-utils/tests/app_chained_multi_evaluator/>`_).
  This means that you can call all the subcommands on the command line in a
  row. But this also means that the results generated by these subcommands need
  to be evaluated by the main command provider, and not by each of the
  subcommand providers. So only the main provider needs a
  `click_evaluator_callback` function.

"""

import functools
import importlib.resources
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

import click
import pydantic
from loguru import logger

from .errors import AutopilotFailure
from .results import RESULTS, ResultHandler, ResultsCollector
from .subprocess import AutopilotSubprocessFailure
from .types import (
    ClickCommandProvider,
    ClickSubCommandProvider,
)


def read_version_from_package(
    package_with_version_file: str, version_file: str = "_version.txt"
):
    """
    Return a function which reads a version number from a file in a Python package.

    To be used as `version_callback` in the :py:func:`make_autopilot_app` function.

    Example::

        cli = make_autopilot_app(
            provider=MyCliClass,
            version_callback=read_version_from_package("mycompany.mypackage", "version.txt"),
        )
    """

    def wrapped_function():
        logger.debug(
            "Reading version from file '{file}' in package '{package}'",
            file=version_file,
            package=package_with_version_file,
        )
        version = importlib.resources.read_text(package_with_version_file, version_file)
        logger.debug("Version is: {v}", v=version)
        return version

    return wrapped_function


class DebugOption(click.Option):
    def handle_parse_result(self, ctx, opts, args):
        ctx.debug = opts.get("debug", False)  # type: ignore
        return super().handle_parse_result(ctx, opts, args)


def make_autopilot_app(
    provider: ClickCommandProvider,
    *,
    version_callback: Callable[[], str],
    allow_chaining: bool = True,
):
    """
    Create a click application from a special type of class/module.

    Parameters
    ----------
    provider: a class or module which provides necessary attributes, e.g.,
      `click_setup`, `click_command`, `click_help_text`, ...
    version_callback: a function which returns the version number of
      the app. See also: :py:func:`read_version_from_package`.
    allow_chaining: Flag to enable or disable the possibility to run
      multiple evaluators as chained subcommands, so that the main CLI
      provider does an overall evaluation of the aggregated results of
      all subcommands. If this is disabled, only one subcommand at a time
      can be used.
    """
    click_subcommands = getattr(provider, "click_subcommands", [])
    click_setup = getattr(provider, "click_setup", [])
    click_command = getattr(provider, "click_command", None)
    click_help_text = getattr(provider, "click_help_text", "")
    if not click_subcommands and not click_command:
        raise TypeError(
            f"CLI provider '{provider}' must provide either "
            "'click_command' or 'click_subcommands'"
        )

    def set_up_logging(debug: bool, colors: bool = False):
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if debug:
            log_level = "DEBUG"
        logger.remove()
        if log_level == "DEBUG":
            logger.add(
                sys.stdout,
                format="<lvl>{time:HH:mm:ss} | {level:5} | {message}</lvl>",
                level="DEBUG",
                backtrace=False,
                diagnose=True,
                colorize=colors,
            )
        else:
            logger.add(
                sys.stdout,
                format="<lvl>{level:5} | {message}</lvl>",
                level="INFO",
                colorize=colors,
                backtrace=False,
                diagnose=False,
            )

    def print_version(ctx: click.Context, param: click.Parameter, value: Any):
        if not value or ctx.resilient_parsing:
            return
        set_up_logging(ctx.debug)  # type: ignore
        version = version_callback()
        click.echo(version.strip())
        ctx.exit()

    def main_cli_entrypoint_wrapper(ctx, colors: bool, debug: bool, *args, **kwargs):
        ctx.color = colors  # necessary for click
        set_up_logging(ctx.debug, colors)
        if click_command:
            click_command(*args, **kwargs)

    def decorator_builder(f):
        decorators: List[Any] = [_handle_click_command_errors]

        has_click_subcommands = click_subcommands is not None and len(click_subcommands) > 0
        if has_click_subcommands:
            decorators.append(
                click.group(
                    name=provider.click_name,
                    help=click_help_text,
                    invoke_without_command=False,
                    no_args_is_help=True,
                    chain=allow_chaining,
                    result_callback=handle_app_result(provider) if allow_chaining else None,
                ),
            )
        else:
            decorators.append(
                click.command(
                    name=provider.click_name,
                    help=click_help_text,
                    no_args_is_help=False,
                )
            )
        decorators.extend(
            [
                click.option(
                    "--version",
                    is_flag=True,
                    callback=print_version,
                    cls=DebugOption,
                    expose_value=False,
                    is_eager=True,
                    help="Output version information and exit.",
                ),
                click.option(
                    "--colors/--no-colors",
                    default=True,
                    help="Enable or disable colors in output.",
                ),
                click.option(
                    "--debug",
                    help="Show debug log messages.",
                    is_flag=True,
                    default=False,
                ),
                *click_setup,
                click.pass_context,
                handle_app_errors,
            ]
        )
        if not has_click_subcommands or allow_chaining:
            if click_command is not None:
                decorators.append(handle_app_result_decorator(provider))
        return functools.reduce(lambda x, dec: dec(x), decorators[::-1], f)

    main_cli_entrypoint = decorator_builder(main_cli_entrypoint_wrapper)
    for i in range(len(click_subcommands)):
        _add_app_subcommand(
            click_subcommands[i], main_cli_entrypoint, handle_results=not allow_chaining
        )
    return main_cli_entrypoint


def _handle_results(
    results: ResultsCollector, provider: ClickCommandProvider | ClickSubCommandProvider
):
    if len(results):
        callback: Optional[ResultHandler] = getattr(provider, "click_evaluator_callback", None)

        if callback is None:
            raise TypeError(
                f"RESULTS were collected, but no function click_evaluator_callback was provided in {provider}!"
            )
        try:
            status, reason = callback(results)
        except (TypeError, ValueError) as e:
            raise TypeError(
                f"The function 'click_evaluator_callback' in {provider} must have the following signature:\n"
                "  click_evaluator_callback(results: ResultsCollector) -> Tuple[status: str, reason: str]"
            ) from e
        if status not in ("GREEN", "YELLOW", "RED", "FAILED"):
            raise ValueError(
                f"The function 'click_evaluator_callback' in {provider} must return a tuple with (status, reason).\n"
                f"The status must be one of GREEN, YELLOW, RED, or FAILED.\nThe returned status was: {status}."
            )
        print(json.dumps({"status": status, "reason": reason}))
        print(results.to_json())
    else:
        logger.debug("RESULTS of {provider} are empty.", provider=provider)


def _handle_click_command_errors(f: Callable):
    """Return decorator for applying the click UsageErrorHandler to a click.command."""
    return ClickUsageErrorHandlerDecorator(f)


class ClickUsageErrorHandlerDecorator:
    """
    Special decorator which wraps around the outermost `click.command` decorator.

    This wrapping is done automatically by the :py:func:`make_autopilot_app` function.

    This is a necessary workaround, because input parameter validation
    happens inside `click.command`. Usually, `click` handles parameter validation
    errors on its own, e.g. when a `click.UsageError` or `click.BadParameter` is
    raised inside a validation `callback` function, it automatically prints out
    the command usage instructions, together with the exception's message and
    exits with an exit code of 2.

    However we want to deal with usage errors differently in our autopilot interface:

    1. we want to exit with code 0 in case of *expected* errors
    2. we want to print out a JSON line with `status=FAILED` and a proper reason.

    This is why this decorator exists: it wraps the outermost `click.command`
    (or `click.group`) decorator and simply forwards all unknown accesses to our
    object to the underlying `f` object (inside the `__getattr__` function).

    There are two possible invocation paths:

    1. In case of testing, the `main` attribute is called.
    2. In case of normal CLI invocation, the object/function is simply called.

    That's why accesses to `main` are rerouted as well as `__call__` (in case
    the class is called).

    Both accesses call the `_wrapper_error_handler` method, which has the
    correct logic for filtering out `SystemExit` exceptions which have a
    `__context__` coming from a `click.exceptions.UsageError`.

    Only in this case, the exception is modified to become a `SystemExit(0)` and
    a proper JSON line is printed.

    All other cases are simply re-raised at the end of the
    `_wrapper_error_handler` method.
    """

    def __init__(self, f):
        self._f = f

    def _wrapper_error_handler(self, *args, **kwargs):
        try:
            self._f.main(*args, **kwargs)
        except SystemExit as e:
            if e.code != 0 and isinstance(e.__context__, click.exceptions.UsageError):
                print(json.dumps({"status": "FAILED", "reason": str(e.__context__)}))
                raise SystemExit(0)
            raise

    def __call__(self, *args, **kwargs):
        return self._wrapper_error_handler(*args, **kwargs)

    @property
    def main(self):
        return self._wrapper_error_handler

    def __getattr__(self, name):
        return getattr(self._f, name)


def handle_app_result_decorator(
    provider: ClickCommandProvider | ClickSubCommandProvider,
):
    def wrapper(f):
        @functools.wraps(f)
        def result_handler_decorator(*args, **kwargs) -> None:
            f(*args, **kwargs)
            _handle_results(RESULTS, provider)

        return result_handler_decorator

    return wrapper


def handle_app_result(
    provider: ClickCommandProvider,
):
    def result_handler_function(subcommand_results: List[Any], **inputs: Dict[str, Any]):
        _handle_results(RESULTS, provider)

    return result_handler_function


def handle_app_errors(f):
    @functools.wraps(f)
    def error_handler(*args, **kwargs):
        try:
            f(*args, **kwargs)
        except pydantic.ValidationError as e:
            error_messages = []
            for error in e.errors():
                msg = f"Input validation failed for {error['loc']}: {error['msg']}."
                error_messages.append(msg)
                logger.error(msg)
            print(json.dumps({"status": "FAILED", "reason": "\n".join(error_messages)}))
            sys.exit(0)
        except AutopilotFailure as e:
            logger.opt(depth=3, exception=True).error("An error has occurred.")
            print(json.dumps({"status": "FAILED", "reason": str(e)}))
            sys.exit(0)
        except AutopilotSubprocessFailure:
            sys.exit(0)
        except Exception:
            logger.opt(depth=3, exception=True).error("An unexpected error has occurred.")
            sys.exit(1)

    return error_handler


def _add_app_subcommand(
    provider: ClickSubCommandProvider,
    click_group: click.Group,
    handle_results: bool,
):
    click_setup = getattr(provider, "click_setup", None)
    if click_setup is None:
        click_setup = []

    def decorator_builder(f):
        click_help_text = getattr(provider, "click_help_text", None)
        decorators = [
            click.command(provider.click_name, help=click_help_text),
            *click_setup,
            handle_app_errors,
        ]
        if handle_results:
            decorators.append(handle_app_result_decorator(provider))
        return functools.reduce(lambda x, dec: dec(x), decorators[::-1], f)

    click_command = getattr(provider, "click_command", None)
    if not click_command:
        raise TypeError("Click subcommand provider must have a 'click_command' function!")
    subcommand = decorator_builder(click_command)
    click_group.add_command(subcommand)
    return subcommand
