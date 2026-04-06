from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class UserQuota:
    user_id: str
    free_balance: int
    paid_balance: int
    last_refresh: int

    @property
    def total_balance(self) -> int:
        return self.free_balance + self.paid_balance


@dataclass(slots=True)
class ChargeResult:
    before: UserQuota
    after: UserQuota
    charged_free: int
    charged_paid: int


class BillingDB:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_effective_user_quota(
        self,
        user_id: str,
        free_quota_amount: int,
        refresh_hours: int,
        now_ts: int,
    ) -> UserQuota:
        with self._connect() as conn:
            quota = self._get_or_create_user_quota(
                conn=conn,
                user_id=user_id,
                free_quota_amount=free_quota_amount,
                now_ts=now_ts,
            )
            quota = self._refresh_user_quota_if_needed(
                conn=conn,
                quota=quota,
                free_quota_amount=free_quota_amount,
                refresh_hours=refresh_hours,
                now_ts=now_ts,
            )
            conn.commit()
            return quota

    def apply_usage_charge(
        self,
        user_id: str,
        amount: int,
        free_quota_amount: int,
        refresh_hours: int,
        now_ts: int,
    ) -> ChargeResult:
        safe_amount = max(0, int(amount))
        with self._connect() as conn:
            quota = self._get_or_create_user_quota(
                conn=conn,
                user_id=user_id,
                free_quota_amount=free_quota_amount,
                now_ts=now_ts,
            )
            quota = self._refresh_user_quota_if_needed(
                conn=conn,
                quota=quota,
                free_quota_amount=free_quota_amount,
                refresh_hours=refresh_hours,
                now_ts=now_ts,
            )
            before = quota
            charged_free = 0
            charged_paid = 0

            if safe_amount > 0:
                if quota.free_balance > 0:
                    charged_free = min(quota.free_balance, safe_amount)
                    quota = UserQuota(
                        user_id=quota.user_id,
                        free_balance=quota.free_balance - charged_free,
                        paid_balance=quota.paid_balance,
                        last_refresh=quota.last_refresh,
                    )
                elif quota.paid_balance > 0:
                    charged_paid = min(quota.paid_balance, safe_amount)
                    quota = UserQuota(
                        user_id=quota.user_id,
                        free_balance=quota.free_balance,
                        paid_balance=quota.paid_balance - charged_paid,
                        last_refresh=quota.last_refresh,
                    )

            self._write_user_quota(conn, quota)
            conn.commit()
            return ChargeResult(
                before=before,
                after=quota,
                charged_free=charged_free,
                charged_paid=charged_paid,
            )

    def record_redeem(
        self,
        order_id: str,
        user_id: str,
        amount: float,
        paid_balance: int,
        source: str,
        free_quota_amount: int,
        refresh_hours: int,
        now_ts: int,
    ) -> UserQuota:
        safe_paid_balance = max(0, int(paid_balance))
        with self._connect() as conn:
            if self._redeemed_order_exists(conn, order_id):
                raise ValueError("该订单已兑换过。")

            quota = self._get_or_create_user_quota(
                conn=conn,
                user_id=user_id,
                free_quota_amount=free_quota_amount,
                now_ts=now_ts,
            )
            quota = self._refresh_user_quota_if_needed(
                conn=conn,
                quota=quota,
                free_quota_amount=free_quota_amount,
                refresh_hours=refresh_hours,
                now_ts=now_ts,
            )
            updated_quota = UserQuota(
                user_id=quota.user_id,
                free_balance=quota.free_balance,
                paid_balance=quota.paid_balance + safe_paid_balance,
                last_refresh=quota.last_refresh,
            )
            self._write_user_quota(conn, updated_quota)
            conn.execute(
                """
                INSERT INTO redeemed_orders
                (order_id, user_id, amount, paid_balance, source, redeem_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, user_id, float(amount), safe_paid_balance, source, now_ts),
            )
            conn.commit()
            return updated_quota

    def list_user_quotas(
        self,
        user_id: str | None = None,
        limit: int | None = None,
    ) -> list[UserQuota]:
        query = """
            SELECT user_id, free_balance, paid_balance, last_refresh
            FROM user_quotas
        """
        params: list[object] = []
        if user_id:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY user_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_user_quota(row) for row in rows]

    def refresh_user_free_quota(
        self,
        user_id: str,
        free_quota_amount: int,
        now_ts: int,
    ) -> UserQuota:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, free_balance, paid_balance, last_refresh
                FROM user_quotas
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                raise ValueError("未找到该用户的额度记录。")

            quota = self._row_to_user_quota(row)
            refreshed = UserQuota(
                user_id=quota.user_id,
                free_balance=max(0, int(free_quota_amount)),
                paid_balance=quota.paid_balance,
                last_refresh=int(now_ts),
            )
            self._write_user_quota(conn, refreshed)
            conn.commit()
            return refreshed

    def refresh_all_users_free_quota(
        self,
        free_quota_amount: int,
        now_ts: int,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE user_quotas
                SET free_balance = ?, last_refresh = ?
                """,
                (max(0, int(free_quota_amount)), int(now_ts)),
            )
            conn.commit()
            return max(0, int(cursor.rowcount))

    def list_redeemed_orders(
        self,
        user_id: str | None = None,
        order_id: str | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT order_id, user_id, amount, paid_balance, source, redeem_time
            FROM redeemed_orders
        """
        conditions: list[str] = []
        params: list[object] = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if order_id:
            conditions.append("order_id = ?")
            params.append(order_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY redeem_time DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))

        with self._connect() as conn:
            return conn.execute(query, tuple(params)).fetchall()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_quotas (
                    user_id TEXT PRIMARY KEY,
                    free_balance INTEGER NOT NULL,
                    paid_balance INTEGER NOT NULL,
                    last_refresh INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS redeemed_orders (
                    order_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    paid_balance INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    redeem_time INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_redeemed_orders_user_id ON redeemed_orders(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_redeemed_orders_redeem_time ON redeemed_orders(redeem_time)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_redeemed_orders_source ON redeemed_orders(source)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_or_create_user_quota(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        free_quota_amount: int,
        now_ts: int,
    ) -> UserQuota:
        row = conn.execute(
            """
            SELECT user_id, free_balance, paid_balance, last_refresh
            FROM user_quotas
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row:
            return self._row_to_user_quota(row)

        quota = UserQuota(
            user_id=user_id,
            free_balance=max(0, int(free_quota_amount)),
            paid_balance=0,
            last_refresh=int(now_ts),
        )
        self._write_user_quota(conn, quota)
        return quota

    def _refresh_user_quota_if_needed(
        self,
        conn: sqlite3.Connection,
        quota: UserQuota,
        free_quota_amount: int,
        refresh_hours: int,
        now_ts: int,
    ) -> UserQuota:
        refresh_seconds = max(0, int(refresh_hours)) * 3600
        if refresh_seconds <= 0:
            return quota
        if now_ts - quota.last_refresh < refresh_seconds:
            return quota

        refreshed = UserQuota(
            user_id=quota.user_id,
            free_balance=max(0, int(free_quota_amount)),
            paid_balance=quota.paid_balance,
            last_refresh=int(now_ts),
        )
        self._write_user_quota(conn, refreshed)
        return refreshed

    def _redeemed_order_exists(self, conn: sqlite3.Connection, order_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM redeemed_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return row is not None

    def _write_user_quota(self, conn: sqlite3.Connection, quota: UserQuota) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO user_quotas
            (user_id, free_balance, paid_balance, last_refresh)
            VALUES (?, ?, ?, ?)
            """,
            (
                quota.user_id,
                quota.free_balance,
                quota.paid_balance,
                quota.last_refresh,
            ),
        )

    @staticmethod
    def _row_to_user_quota(row: sqlite3.Row) -> UserQuota:
        return UserQuota(
            user_id=str(row["user_id"]),
            free_balance=int(row["free_balance"]),
            paid_balance=int(row["paid_balance"]),
            last_refresh=int(row["last_refresh"]),
        )
