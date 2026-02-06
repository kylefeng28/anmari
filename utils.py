def decode_if_bytes(maybe_bytes):
    if isinstance(maybe_bytes, bytes):
        return maybe_bytes.decode()
    else:
        return str(maybe_bytes)
