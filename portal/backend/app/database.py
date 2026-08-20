from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_user_identity_columns() -> None:
    """Add the identity columns (email, display_name) that ``users`` gained
    after the database was first created.

    There is no migration tool in this project: main.py calls
    Base.metadata.create_all, which creates missing TABLES but never missing
    COLUMNS. Running this twice is a no-op.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("users")}
    additions = (
        ("email", "ALTER TABLE users ADD COLUMN email VARCHAR(255)"),
        ("display_name", "ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"),
    )
    missing = [(column, ddl) for column, ddl in additions if column not in existing]
    if missing:
        with engine.begin() as conn:
            for _column, ddl in missing:
                conn.execute(text(ddl))
