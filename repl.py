#!/usr/bin/env python3
"""
Interactive REPL for anmari email client
"""

import shlex
import click


def repl(cli):
    """Start interactive REPL mode"""
    print("anmari interactive mode")
    print("Type 'help' for commands, 'exit' or Ctrl+D to quit\n")

    while True:
        try:
            # Read command
            line = input("anmari> ").strip()

            if not line:
                continue

            # Handle exit commands
            if line in ('exit', 'quit', 'q'):
                print("Goodbye!")
                break

            # Parse command line into argv
            try:
                argv = shlex.split(line)
            except ValueError as e:
                print(f"Error parsing command: {e}")
                continue

            # Execute command by calling the CLI with parsed args
            try:
                # Invoke the CLI with the parsed arguments
                # standalone_mode=False prevents sys.exit() calls
                cli(argv, standalone_mode=False)

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
