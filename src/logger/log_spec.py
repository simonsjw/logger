#!/usr/bin/env python3
"""
Specification for logs table model.
"""

from infopypg import Base
from typing import Any
from datetime import datetime
from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Text,
    func,
    Identity,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    Mapped,
    mapped_column
)


class Logs(Base):
    """
    Table for logging records (partitioned by tstamp for growth).

    Refinements:
    - idx: BIGINT IDENTITY (autoincrement per partition); composite PK (idx, tstamp) for uniqueness.
      Sub-partitions: Create post-setup, e.g., FOR VALUES FROM ('YYYY-MM-DD') TO ('YYYY-MM-DD+1').
    - Sorting: Index on tstamp for ORDER BY.
    - JSONB: dict[str, Any] for obj (structured extra data); nullable for flexibility.
    """
    __tablename__: str = "logs"

    idx: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),  # Per-partition autoincrement.
        primary_key=True,
    )
    tstamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), primary_key=True
    )  # Partition key; part of PK.
    loglvl: Mapped[str] = mapped_column(Text, nullable=False)
    logger: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    obj: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_logs_tstamp", "tstamp"),
        {"extend_existing": True},
    )
