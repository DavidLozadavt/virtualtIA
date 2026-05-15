"""
core/database.py — Pool de conexiones MySQL con PyMySQL.

Provee get_connection() como context manager para usar en cada request.
"""

import pymysql
from pymysql.cursors import DictCursor
from contextlib import contextmanager
from core.config import settings
import logging

logger = logging.getLogger("lyra.db")

_pool_config = {
    "host": settings.DB_HOST,
    "port": settings.DB_PORT,
    "user": settings.DB_USER,
    "password": settings.DB_PASS,
    "database": settings.DB_NAME,
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
    "autocommit": True,
}


@contextmanager
def get_connection(database_name: str = None):
    """
    Yields a PyMySQL connection. Closes it on exit.
    Allows specifying a different database name dynamically.
    """
    config = _pool_config.copy()
    if database_name:
        config["database"] = database_name
        
    conn = None
    try:
        conn = pymysql.connect(**config)
        yield conn
    except pymysql.MySQLError as e:
        logger.error(f"MySQL error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def run_migration(sql_path: str) -> None:
    """Execute a .sql file against the database."""
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()

    with get_connection() as conn:
        with conn.cursor() as cursor:
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
    logger.info(f"Migration {sql_path} applied successfully.")


def check_connection() -> bool:
    """Quick ping to verify MySQL is reachable."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                return True
    except Exception:
        return False
