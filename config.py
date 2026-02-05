import platform
import tomllib
from pathlib import Path
import click


# Config paths
def get_config_path() -> Path:
    """Get config file path"""
    os_name = platform.system()

    # macOS
    if os_name == 'Darwin':
        return Path.home() / "Library/Application Support/anmari/config.toml"
    # Linux/Unix
    else:
        return Path.home() / ".config/anmari/config.toml"


class AccountConfig:
    def __init__(self, account):
        """Load config from TOML file"""
        config_path = get_config_path()
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found at {config_path}")

        with open(config_path, "rb") as f:
            self._config = tomllib.load(f)

        if account >= len(self._config['accounts']):
            click.echo(f"Error: Account {account} not found", err=True)
            return

        self._acc = self._config['accounts'][account]

    def get(self, key, default=None):
        return self._acc.get(key, default)

    def get_password(self):
        password = self.get('password')
        if not password:
            click.echo("Error: No password configured", err=True)
            return
        return password

