import os
from contextlib import contextmanager

from psycopg2.pool import SimpleConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool: SimpleConnectionPool | None = None


def init_db():
    """
    Inicializa el pool de conexiones. Se llama en el evento startup de FastAPI.
    """
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL no está definida")
        _pool = SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
        )
        print("✅ Pool de conexiones a Postgres inicializado")


def close_db():
    """
    Cierra todas las conexiones del pool. Se llama en el evento shutdown.
    """
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        print("🛑 Pool de conexiones cerrado")


@contextmanager
def get_db():
    """
    Context manager para obtener una conexión del pool.
    Uso:

        from .db import get_db

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
    """
    global _pool
    if _pool is None:
        raise RuntimeError("Pool de conexiones no inicializado. Llama a init_db() primero.")
    conn = _pool.getconn()
    try:
        conn.autocommit = True  # para no preocuparnos de commit/rollback por ahora
        yield conn
    finally:
        _pool.putconn(conn)
