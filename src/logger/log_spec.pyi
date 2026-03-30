from datetime import datetime as datetime
from infopypg import Base
from sqlalchemy import Index
from sqlalchemy.orm import Mapped as Mapped
from typing import Any

class Logs(Base):
    __tablename__: str
    idx: Mapped[int]
    tstamp: Mapped[datetime]
    loglvl: Mapped[str]
    logger: Mapped[str]
    message: Mapped[str]
    obj: Mapped[dict[str, Any] | None]
    __table_args__: tuple[Index, dict[str, bool]]
    trigger_sql: str
