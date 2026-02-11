import random
import string
import base64
from prompt_toolkit import prompt

# version prefix 1. + a short header segment + a long base64url-encoded-style payload
def generate_mock_auth_code():
    header = ''.join(random.choices(string.ascii_letters + string.digits + '-_', k=random.randint(15, 25)))
    payload_length = random.randint(800, 1200)
    payload = ''.join(random.choices(string.ascii_letters + string.digits + '-_', k=random.randint(1800, 2200)))

    return f'1.{header}.{payload}'

if __name__ == "__main__":
    print(generate_mock_auth_code())

    print()
    print('Try copying and pasting here:')
    # These will hang in macOS in both iTerm 2 and Terminal.app when the user pastes the code for some reason
    # a = input('>')
    # a = sys.input.readline('>')

    # prompt_toolkit seems to work fine, probably because it uses asyncio?
    a = prompt('> ')

    print()
    print()
    print('User typed: ')
    print(a)
