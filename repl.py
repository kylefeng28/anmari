#!/usr/bin/env python3
"""
Interactive REPL for anmari email client
"""

import shlex
import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from pathlib import Path


class PipeContext:
    """Context for passing data between piped commands"""

    query: str

    def __init__(self):
        self.query = None

    def set_query(self, query):
        self.query = query

    def has_query(self):
        return self.query is not None

    def clear(self):
        self.query = None


def chain_query(argv, pipe_ctx):
    if argv[0] in ['queue']:
        argv.extend(pipe_ctx.query)
    else:
        raise click.UsageError(f'pipe not supported for {argv[0]}')

def parse_pipe_chain(line):
    """Parse command line into pipe chain

    Returns: List of command strings
    Example: "search foo | queue move" -> ["search foo", "queue move"]
    """
    # Split on | but respect quotes
    parts = []
    current = []
    in_quotes = False
    quote_char = None

    for char in line:
        if char in ('"', "'") and (not in_quotes or char == quote_char):
            in_quotes = not in_quotes
            quote_char = char if in_quotes else None
            current.append(char)
        elif char == '|' and not in_quotes:
            if current:
                parts.append(''.join(current).strip())
                current = []
        else:
            current.append(char)

    if current:
        parts.append(''.join(current).strip())

    return parts

def repl(cli):
    """Start interactive REPL mode with prompt_toolkit"""

    # Setup history file
    history_file = Path.home() / '.anmari_history'
    session = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=WordCompleter([
            'sync', 'search', 'tag', 'folders', 'cleanup',
            'queue', 'status', 'apply', 'help', 'exit', 'quit',
            '--account', '--folder', '--limit', '--all',
            '--dry-run', '--to', '--add', '--remove'
        ], ignore_case=True)
    )

    print("anmari interactive mode")
    print("Type 'help' for commands, 'exit' or Ctrl+D to quit\n")
    print("Use | to pipe results: search tag:foo | queue markread\n")

    pipe_ctx = PipeContext()

    def _execute(argv):
        # Execute command by calling the CLI with parsed args
        try:
            # Invoke the CLI with the parsed arguments
            # standalone_mode=False prevents sys.exit() calls
            cli(argv, standalone_mode=False, obj=pipe_ctx)

        except SystemExit as e:
            # Click calls sys.exit(), catch it to keep REPL running
            if e.code != 0 and e.code is not None:
                print(f"Command exited with code {e.code}")
        except click.ClickException as e:
            e.show()
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

    while True:
        try:
            # Read command with prompt_toolkit
            line = session.prompt("anmari> ").strip()

            if not line:
                continue

            # Handle exit commands
            if line in ('exit', 'quit', 'q'):
                print("Goodbye!")
                break

            # Parse pipe chain
            commands = parse_pipe_chain(line)

            # Execute command chain
            for i, cmd_str in enumerate(commands):
                is_first = i == 0
                is_last = i == len(commands) - 1

                # Parse command into argv
                try:
                    argv = shlex.split(cmd_str)
                except ValueError as e:
                    print(f"Error parsing command: {e}")
                    break

                # Support aliases (e.g. markread -> queue markread)
                if argv[0] in ('archive', 'markread', 'markunread', 'label', 'move', 'status'):
                    argv = ['queue'] + argv

                # Inject pipe context for non-first commands
                try:
                    if not is_first and pipe_ctx.has_query():
                        chain_query(argv, pipe_ctx)
                except click.UsageError as e:
                    print(f"Error parsing command: {e}")
                    break

                _execute(argv)

                # Clear pipe context after last command
                if is_last:
                    pipe_ctx.clear()

        except EOFError:
            # Ctrl+D pressed
            print("\nGoodbye!")
            break
        except KeyboardInterrupt:
            # Ctrl+C pressed
            print("\n(Use 'exit' or Ctrl+D to quit)")
            continue


if __name__ == '__main__':
    from anmari import cli
    repl(cli)
