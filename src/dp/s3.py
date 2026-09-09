"""S3 bucket operations for temporary parquet artifacts."""

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from aiobotocore.session import AioSession, ClientCreatorContext, get_session

from .settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from types_aiobotocore_s3.client import S3Client
    from types_aiobotocore_s3.type_defs import ObjectIdentifierTypeDef


def create_s3_client(
    session: AioSession, endpoint: str
) -> ClientCreatorContext[S3Client]:
    """Create a typed S3 client context"""
    create_client = cast(
        "Callable[..., ClientCreatorContext[S3Client]]",
        session.create_client,
    )

    return create_client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.GCS_KEY_ID,
        aws_secret_access_key=settings.GCS_SECRET_KEY,
        region_name="us-east-1",
    )


async def clear_bucket() -> None:
    """Delete all objects from the configured GCS bucket"""
    endpoint = f"http{'s' if settings.GCS_USE_SSL else ''}://{settings.GCS_ENDPOINT}"

    async with create_s3_client(get_session(), endpoint) as client:
        paginator = client.get_paginator("list_objects_v2")

        async for page in paginator.paginate(Bucket=settings.GCS_BUCKET):
            objects = page.get("Contents", [])

            keys: list[ObjectIdentifierTypeDef] = [
                {"Key": key} for obj in objects if (key := obj.get("Key")) is not None
            ]

            if not keys:
                continue

            await client.delete_objects(
                Bucket=settings.GCS_BUCKET,
                Delete={"Objects": keys},
            )
