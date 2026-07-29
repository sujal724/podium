"""Short, prefixed, collision-resistant ids: new_id("s") -> "s_7f3a9c"."""

import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(3)}"
