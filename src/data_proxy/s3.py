"""S3 bucket operations for temporary parquet artifacts."""

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from aiobotocore.session import AioSession, ClientCreatorContext, get_session

from .log import logger
from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from types_aiobotocore_s3.client import S3Client
    from types_aiobotocore_s3.type_defs import ObjectIdentifierTypeDef


def create_s3_client(
    session: AioSession, endpoint: str
) -> ClientCreatorContext[S3Client]:
    """Create a typed S3 client context."""
    create = cast(
        "Callable[..., ClientCreatorContext[S3Client]]",
        session.create_client,
    )

    return create(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
    )


async def clear_s3_prefix(prefix: str) -> None:
    """Delete temporary objects under one S3 key prefix."""
    endpoint = f"http{'s' if settings.S3_USE_SSL else ''}://{settings.S3_ENDPOINT}"

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
            if keys:
                await client.delete_objects(
                    Bucket=settings.S3_BUCKET, Delete={"Objects": keys}
                )


async def clear_s3_bucket() -> None:
    """Delete every object from the configured bucket, keeping the bucket itself."""
    endpoint = f"http{'s' if settings.S3_USE_SSL else ''}://{settings.S3_ENDPOINT}"

    async with create_s3_client(get_session(), endpoint) as client:
        paginator = client.get_paginator("list_objects_v2")

        async for page in paginator.paginate(Bucket=settings.S3_BUCKET):
            objects = page.get("Contents", [])

            keys: list[ObjectIdentifierTypeDef] = [
                {"Key": key} for obj in objects if (key := obj.get("Key")) is not None
            ]

            if not keys:
                continue

            await client.delete_objects(
                Bucket=settings.S3_BUCKET,
                Delete={"Objects": keys},
            )

    logger.info("Bucket emptied")
