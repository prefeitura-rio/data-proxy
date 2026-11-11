"""S3 bucket operations for temporary parquet artifacts."""

from typing import TYPE_CHECKING

from aiobotocore.session import AioSession, ClientCreatorContext, get_session

from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from types_aiobotocore_s3.client import S3Client
    from types_aiobotocore_s3.type_defs import ObjectIdentifierTypeDef


SHOWN_KEYS = 5
"""How many refused keys the error names."""


class S3DeleteError(Exception):
    """The object store refused to delete one or more objects."""


def create_s3_client(
    session: AioSession, endpoint: str
) -> ClientCreatorContext[S3Client]:
    """Create a typed S3 client context."""
    return session.create_client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY.get_secret_value(),
        region_name=settings.S3_REGION,
    )


async def clear_s3_prefix(prefix: str) -> None:
    """Delete temporary objects under one S3 key prefix.

    S3 reports a refused key in the reply and not as an error, so the function collects them,
    deletes every page, and then raises once.
    """
    endpoint = f"http{'s' if settings.S3_USE_SSL else ''}://{settings.S3_ENDPOINT}"

    refused: list[str] = []

    async with create_s3_client(get_session(), endpoint) as client:
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(
            Bucket=settings.S3_BUCKET, Prefix=prefix.rstrip("/") + "/"
        ):
            keys: list[ObjectIdentifierTypeDef] = [
                {"Key": key}
                for obj in page.get("Contents", [])
                if (key := obj.get("Key")) is not None
            ]
            if len(keys) > 0:
                reply = await client.delete_objects(
                    Bucket=settings.S3_BUCKET, Delete={"Objects": keys}
                )
                refused.extend(
                    f"{item.get('Key')} ({item.get('Code')})"
                    for item in reply.get("Errors", [])
                )

    if len(refused) > 0:
        shown = ", ".join(refused[:SHOWN_KEYS])
        raise S3DeleteError(
            f"The object store refused to delete {len(refused)} objects under {prefix}: {shown}"
        )
