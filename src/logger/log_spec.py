#!/usr/bin/env python3
"""
Specification for logs table model.
"""

from datetime import datetime
from typing import Any

from infopypg import Base
from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


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

    __table_args__: tuple[Index, dict[str, bool]] = (
        Index("ix_logs_tstamp", "tstamp"),
        {"extend_existing": True},
    )

    # this trigger ensures the tstamp is never null.
    trigger_sql: str = """  
    CREATE OR REPLACE FUNCTION set_logs_tstamp()
    RETURNS TRIGGER AS $$
    BEGIN
        IF NEW.tstamp IS NULL THEN
            NEW.tstamp := CURRENT_TIMESTAMP;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    CREATE OR REPLACE TRIGGER logs_set_tstamp
    BEFORE INSERT ON logs
    FOR EACH ROW
    EXECUTE FUNCTION set_logs_tstamp();
    """
