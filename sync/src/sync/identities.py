"""Remove the S3 identities that the console made for schemas that no longer exist."""

import re
from typing import TYPE_CHECKING

from aiobotocore.session import AioSession, ClientCreatorContext, get_session

from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Collection, Iterable

    from types_aiobotocore_iam.client import IAMClient

IDENTITY_PREFIX = "sig_"
IDENTITY_NAME = re.compile(r"^sig_[a-z][a-z0-9_]{0,55}$")


def stale_users(users: Iterable[str], schemas: Collection[str]) -> list[str]:
    """Return the identities of the console whose schema is not in schemas."""
    return sorted(
        user
        for user in users
        if IDENTITY_NAME.fullmatch(user) is not None
        and user.removeprefix(IDENTITY_PREFIX) not in schemas
    )


def create_iam_client(
    session: AioSession, endpoint: str
) -> ClientCreatorContext[IAMClient]:
    """Create a typed IAM client context."""
    return session.create_client(
        "iam",
        endpoint_url=endpoint,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    )


async def list_users(client: IAMClient) -> list[str]:
    """Return the name of every user of the object store."""
    names: list[str] = []

    async for page in client.get_paginator("list_users").paginate():
        names.extend(user["UserName"] for user in page["Users"])

    return names


async def remove_user(client: IAMClient, user: str) -> None:
    """Delete the keys and the policies of one user, and then the user."""
    keys = await client.list_access_keys(UserName=user)

    for key in keys["AccessKeyMetadata"]:
        if "AccessKeyId" in key:
            await client.delete_access_key(
                UserName=user, AccessKeyId=key["AccessKeyId"]
            )

    policies = await client.list_user_policies(UserName=user)

    for policy in policies["PolicyNames"]:
        await client.delete_user_policy(UserName=user, PolicyName=policy)

    await client.delete_user(UserName=user)


async def remove_stale_identities(schemas: Collection[str]) -> list[str]:
    """Delete the identity of each schema that is gone, and return the names of the deleted users."""
    endpoint = f"http{'s' if settings.S3_USE_SSL else ''}://{settings.S3_ENDPOINT}"

    async with create_iam_client(get_session(), endpoint) as client:
        removed = stale_users(await list_users(client), schemas)

        for user in removed:
            await remove_user(client, user)

    return removed
