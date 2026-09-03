"""测试 research_db.migrations — Schema 迁移逻辑。"""

from __future__ import annotations

import sqlite3

from trade_krono_cli.research_db.migrations import migrate_schema

# ── migrate_schema ────────────────────────────────────────────────────────────


class TestMigrateSchema:
    """Schema 迁移测试。"""

    def _create_minimal_db(self, tmp_path) -> sqlite3.Connection:
        """创建只有基础列的数据库。"""
        db_path = tmp_path / "research.db"
        conn = sqlite3.connect(db_path)
        # 创建只含基础列的 jobs 表（模拟旧版本）
        conn.execute(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT,
                created_at REAL
            )
            """,
        )
        conn.commit()
        return conn

    def test_adds_version_columns(self, tmp_path) -> None:
        """迁移应添加 version 相关列。"""
        conn = self._create_minimal_db(tmp_path)
        migrate_schema(conn)
        conn.commit()

        info = conn.execute("PRAGMA table_info(jobs)").fetchall()
        col_names = {row[1] for row in info}

        # 应包含新增的列
        assert "run_id" in col_names
        assert "config_hash" in col_names

    def test_idempotent_migration(self, tmp_path) -> None:
        """重复迁移不应报错。"""
        conn = self._create_minimal_db(tmp_path)
        migrate_schema(conn)
        conn.commit()
        # 再次迁移应成功（不抛异常）
        migrate_schema(conn)
        conn.commit()

    def test_no_op_when_columns_exist(self, tmp_path) -> None:
        """列已存在时迁移应跳过。"""
        conn = self._create_minimal_db(tmp_path)
        # 手动添加 version 列
        conn.execute("ALTER TABLE jobs ADD COLUMN run_id TEXT")
        conn.execute("ALTER TABLE jobs ADD COLUMN config_hash TEXT")
        conn.commit()

        # 迁移应不报错
        migrate_schema(conn)
        conn.commit()

        info = conn.execute("PRAGMA table_info(jobs)").fetchall()
        # 不应有重复列
        col_names = [row[1] for row in info]
        assert col_names.count("run_id") == 1
        assert col_names.count("config_hash") == 1

    def test_non_jobs_table_unchanged(self, tmp_path) -> None:
        """非 jobs 表不应受影响。"""
        conn = self._create_minimal_db(tmp_path)
        # 创建其他表
        conn.execute("CREATE TABLE other_table (id TEXT PRIMARY KEY)")
        conn.commit()

        migrate_schema(conn)
        conn.commit()

        # other_table 应仍然存在
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row[0] for row in tables}
        assert "other_table" in table_names
