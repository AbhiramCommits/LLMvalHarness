"""Create an API key and print the raw key once.

Usage:
    python scripts/create_api_key.py <name> <role>

The raw key is printed to stdout and is never stored; only its salted hash
lands in Postgres. Roles: admin | reviewer.
"""

import asyncio
import secrets
import sys

from app.auth import hash_api_key, key_prefix
from app.db import SessionFactory
from app.models import ApiKey, ApiKeyRole


async def main(name: str, role: str) -> None:
    api_key_role = ApiKeyRole(role)
    raw_key = secrets.token_urlsafe(32)
    api_key = ApiKey(
        name=name,
        role=api_key_role,
        key_prefix=key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
    )
    async with SessionFactory() as session:
        session.add(api_key)
        await session.commit()
    print(raw_key)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <name> <role>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
