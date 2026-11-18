from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.core.db import get_conn
from app.repositories import stats_repo

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/summary")
def summary(conn: Connection = Depends(get_conn)):
    return stats_repo.summary(conn)


@router.get("/by-vendor")
def by_vendor(limit: int = Query(10, ge=1, le=100), conn: Connection = Depends(get_conn)):
    return stats_repo.by_vendor(conn, limit)


@router.get("/by-day")
def by_day(days: int = Query(30, ge=1, le=365), conn: Connection = Depends(get_conn)):
    return stats_repo.by_day(conn, days)


@router.get("/discrepancy-types")
def discrepancy_types(conn: Connection = Depends(get_conn)):
    return stats_repo.discrepancy_types(conn)


@router.get("/currencies")
def currencies(conn: Connection = Depends(get_conn)):
    return stats_repo.currencies(conn)
