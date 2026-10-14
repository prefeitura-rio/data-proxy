"""The console tables, as SQLAlchemy columns. The migrations create the real tables."""

from typing import final

from sqlalchemy import BigInteger, Boolean, Column, DateTime, MetaData, String, Table
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="data_proxy")


@final
class Revisions:
    """The columns and the table of data_proxy.config_revisions."""

    revision_id = Column("id", BigInteger, primary_key=True)
    created_at = Column("created_at", DateTime(timezone=True), nullable=False)
    author = Column("author", String, nullable=False)
    comment = Column("comment", String, nullable=False)
    document = Column("document", JSONB, nullable=False)
    active = Column("active", Boolean, nullable=False)
    table = Table(
        "config_revisions",
        metadata,
        revision_id,
        created_at,
        author,
        comment,
        document,
        active,
    )
