"""存储门面：一个插件实例持有一个 Database 与全部仓储。"""

from __future__ import annotations

from pathlib import Path

from .database import Database
from .repositories import Repositories


class Store:
    """数据库 + 仓储的聚合容器。"""

    def __init__(self, database: Database) -> None:
        self.db = database
        self.repos = Repositories(database)

    @classmethod
    def open(cls, db_path: Path) -> Store:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        database = Database(db_path)
        database.connect()
        database.migrate()
        return cls(database)

    def close(self) -> None:
        self.db.close()
