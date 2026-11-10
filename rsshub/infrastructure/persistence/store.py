"""存储门面：一个插件实例持有一个 Database 与全部仓储。"""

from __future__ import annotations

from pathlib import Path

from .database import Database
from .repositories import Repositories


class Store:
    """数据库 + 仓储的聚合容器。"""

    def __init__(self, database: Database, added_columns: list[str] | None = None) -> None:
        self.db = database
        self.repos = Repositories(database)
        # 打开时按建表脚本补上的列，供插件启动日志说明本次升级改了什么
        self.added_columns: list[str] = added_columns or []

    @classmethod
    def open(cls, db_path: Path) -> Store:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        database = Database(db_path)
        added = database.ensure_schema()
        return cls(database, added)

    def close(self) -> None:
        self.db.close()
