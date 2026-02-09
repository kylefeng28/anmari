"""OAuth2 authentication for Microsoft 365"""
from urllib.parse import urlencode, parse_qs, urlparse
import click


# Microsoft Office public client ID
OUTLOOK_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def get_auth_url(client_id=OUTLOOK_CLIENT_ID, tenant="common"):
    """Generate Microsoft OAuth2 authorization URL"""
    params = {
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': 'https://graph.microsoft.com/.default offline_access',
        'response_mode': 'query'
    }

    base_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    return f"{base_url}?{urlencode(params)}"


def manual_auth_flow(client_id=OUTLOOK_CLIENT_ID, tenant="common"):
    """
    Manual OAuth2 flow matching DavMail's approach.
    User opens URL in browser, completes auth, and pastes back the code.
    """
    auth_url = get_auth_url(client_id, tenant)

    click.echo("\n" + "="*70)
    click.echo("Microsoft 365 Authentication Required")
    click.echo("="*70)
    click.echo("\n1. Open this URL in your browser:")
    click.echo(f"\n   {auth_url}\n")
    click.echo("2. Sign in with your Microsoft account")
    click.echo("3. After authentication, you'll be redirected to a page")
    click.echo("4. Copy the FULL redirect URL from your browser's address bar")
    click.echo("   (It should contain '?code=' in the URL)")
    click.echo("\nAlternatively, use Chrome DevTools:")
    click.echo("   - Open DevTools (F12) -> Network tab")
    click.echo("   - Look for redirect with 'code=' parameter")
    click.echo("="*70 + "\n")

    # Get auth code from user
    redirect_url = click.prompt("\nPaste the redirect URL here").strip()

    # Parse code from URL
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)

    if 'code' not in params:
        raise ValueError("No authorization code found in URL")

    auth_code = params['code'][0]
    click.echo(f"\n✓ Authorization code received: {auth_code[:20]}...")

    return auth_code


def exchange_code_for_token(auth_code, client_id=OUTLOOK_CLIENT_ID, tenant="common"):
    """
    Exchange authorization code for access token.
    Note: For production, you'd need a client secret or use PKCE flow.
    """
    import requests

    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    data = {
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'code': auth_code,
        'grant_type': 'authorization_code',
        'scope': 'https://graph.microsoft.com/.default offline_access'
    }

    response = requests.post(token_url, data=data)
    response.raise_for_status()

    token_data = response.json()
    return token_data


if __name__ == '__main__':
    # Test the auth flow
    click.echo("Testing Microsoft 365 OAuth2 flow...\n")

    try:
        auth_code = manual_auth_flow()
        click.echo("\n✓ Authentication successful!")
        click.echo(f"Authorization code: {auth_code}")

        # Note: Token exchange requires client secret for confidential clients
        # For public clients, use device code flow or PKCE
        click.echo("\nTo exchange for token, use device code flow in production")

    except Exception as e:
        click.echo(f"\n✗ Authentication failed: {e}", err=True)
