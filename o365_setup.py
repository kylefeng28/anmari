#!/usr/bin/env python3
"""Setup script for Microsoft 365 accounts"""
import click
import tomli_w
from pathlib import Path
from o365_auth import manual_auth_flow, OUTLOOK_CLIENT_ID


def get_config_path():
    return Path.home() / ".config/anmari/config.toml"


@click.group()
def cli():
    """Microsoft 365 setup for anmari"""
    pass


@cli.command()
@click.option('--email', required=True, help='Email address')
@click.option('--client-id', default=OUTLOOK_CLIENT_ID, help='Azure AD client ID')
@click.option('--tenant', default='common', help='Azure AD tenant ID')
def add_account(email, client_id, tenant):
    """Add a Microsoft 365 account"""

    click.echo(f"\nSetting up Microsoft 365 account: {email}")

    # Run auth flow
    try:
        auth_code = manual_auth_flow(client_id, tenant)
        click.echo("\n✓ Authentication successful!")

        # Load existing config
        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            import tomllib
            with open(config_path, 'rb') as f:
                config = tomllib.load(f)
        else:
            config = {'accounts': []}

        # Add new account
        account = {
            'email': email,
            'provider': 'microsoft365',
            'client_id': client_id,
            'tenant_id': tenant,
            'cache_days': 90
        }

        # Check if account already exists
        existing = [a for a in config.get('accounts', []) if a.get('email') == email]
        if existing:
            click.echo(f"\n⚠ Account {email} already exists, updating...")
            config['accounts'] = [a for a in config['accounts'] if a.get('email') != email]

        config['accounts'].append(account)

        # Save config
        with open(config_path, 'wb') as f:
            tomli_w.dump(config, f)

        click.echo(f"\n✓ Account added to {config_path}")
        click.echo("\nNext steps:")
        click.echo(f"  1. Run: ./anmari.py sync --account {email}")
        click.echo(f"  2. Search: ./anmari.py search 'is:unread' --account {email}")

    except Exception as e:
        click.echo(f"\n✗ Setup failed: {e}", err=True)
        raise


@cli.command()
def test_auth():
    """Test OAuth2 authentication flow"""
    click.echo("Testing Microsoft 365 authentication...\n")

    try:
        auth_code = manual_auth_flow()
        click.echo("\n✓ Authentication successful!")
        click.echo(f"Authorization code: {auth_code[:50]}...")

    except Exception as e:
        click.echo(f"\n✗ Authentication failed: {e}", err=True)


if __name__ == '__main__':
    cli()
