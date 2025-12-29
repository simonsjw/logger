from datetime import datetime as datetime
from typing import Any

from _typeshed import Incomplete
from infopypg import Base
from sqlalchemy.orm import Mapped as Mapped

class Logs(Base):
    __tablename__: str
    idx: Mapped[int]
    tstamp: Mapped[datetime]
    loglvl: Mapped[str]
    logger: Mapped[str]
    message: Mapped[str]
    obj: Mapped[dict[str, Any] | None]
    __table_args__: Incomplete
