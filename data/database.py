"""
data/database.py
================
The database schema and connection plumbing for Stonks.

WHAT AN "ORM MODEL" IS
    ORM = Object-Relational Mapper. Instead of writing raw SQL strings, we
    describe each database table as a normal Python class (an "ORM model"),
    where each class attribute is a column. SQLAlchemy then translates between
    Python objects and database rows for us: creating a `PriceBar(...)` object
    and saving it becomes an INSERT; querying returns `PriceBar` objects back.
    This keeps all our data definitions in one readable place and lets the rest
    of the codebase work with Python objects instead of SQL.

WHAT THIS MODULE PROVIDES
    - Three table models: PriceBar, Fundamentals, EarningsHistory
    - get_engine()  → the connection to data/stonks.db
    - init_db()     → creates the tables (safe to call repeatedly)
    - get_session() → a session object the writer/reader modules use

The actual database file lives at data/stonks.db (see DB_PATH below).
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# Where the database file lives. Defined here as a single constant so it is
# trivial to point at a different location later (e.g. a shared/cloud path).
# Path(__file__) makes this work no matter which directory we run from.
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent / "stonks.db"
DB_URL = f"sqlite:///{DB_PATH}"

# Base class that all our ORM models inherit from. SQLAlchemy uses it to keep
# track of every table so init_db() can create them all at once.
Base = declarative_base()


class PriceBar(Base):
    """One row = one OHLCV bar (one day, by default) for one ticker."""

    __tablename__ = "price_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, index=True, nullable=False)   # e.g. "AAPL"
    date = Column(Date, index=True, nullable=False)        # the trading day
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger)                            # share counts can be huge
    created_at = Column(DateTime, server_default=func.now())  # when we stored it

    # Never store the same ticker+day twice.
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uix_pricebar_ticker_date"),
    )


class Fundamentals(Base):
    """One row = a snapshot of a ticker's fundamentals on a given fetch date."""

    __tablename__ = "fundamentals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, index=True, nullable=False)
    fetched_date = Column(Date, index=True, nullable=False)  # the day we pulled it

    # All metrics are nullable: yfinance frequently omits some of these.
    forward_pe = Column(Float, nullable=True)
    trailing_pe = Column(Float, nullable=True)
    ev_to_ebitda = Column(Float, nullable=True)
    price_to_fcf = Column(Float, nullable=True)
    forward_eps = Column(Float, nullable=True)
    trailing_eps = Column(Float, nullable=True)
    revenue_growth_yoy = Column(Float, nullable=True)
    earnings_growth_yoy = Column(Float, nullable=True)
    gross_margins = Column(Float, nullable=True)
    operating_margins = Column(Float, nullable=True)
    profit_margins = Column(Float, nullable=True)
    return_on_equity = Column(Float, nullable=True)
    current_ratio = Column(Float, nullable=True)
    debt_to_equity = Column(Float, nullable=True)

    # One fundamentals snapshot per ticker per day.
    __table_args__ = (
        UniqueConstraint("ticker", "fetched_date", name="uix_fund_ticker_date"),
    )


class EarningsHistory(Base):
    """One row = one reported quarter's EPS estimate vs actual for a ticker."""

    __tablename__ = "earnings_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, index=True, nullable=False)
    report_date = Column(Date, nullable=False)              # the earnings date
    eps_estimate = Column(Float, nullable=True)
    eps_actual = Column(Float, nullable=True)
    surprise_pct = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("ticker", "report_date", name="uix_earn_ticker_date"),
    )


# ---------------------------------------------------------------------------
# Engine / session helpers.
# We cache a single engine (and session factory) at module level so the whole
# app shares one connection pool instead of re-opening the file repeatedly.
# ---------------------------------------------------------------------------
_engine = None
_SessionFactory = None


def get_engine():
    """Create (first call) or return the existing SQLAlchemy engine for stonks.db."""
    global _engine
    if _engine is None:
        # echo=False keeps SQL out of the console; flip to True to debug queries.
        _engine = create_engine(DB_URL, echo=False, future=True)
    return _engine


def init_db():
    """Create every table that doesn't already exist. Safe to call repeatedly."""
    engine = get_engine()
    Base.metadata.create_all(engine)  # create_all is a no-op for existing tables
    return engine


def get_session():
    """Return a new SQLAlchemy session bound to the stonks.db engine.

    Other modules (db_writer, db_reader) call this to talk to the database.
    Callers are responsible for closing the session when done.
    """
    global _SessionFactory
    engine = get_engine()
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=engine, future=True)
    return _SessionFactory()


if __name__ == "__main__":
    # Self-test: build the database and report what was created.
    init_db()

    print(f"Database URL : {DB_URL}")
    print(f"File path    : {DB_PATH}")
    print(f"File created : {DB_PATH.exists()}")

    tables = inspect(get_engine()).get_table_names()
    print(f"Tables created ({len(tables)}): {tables}")
