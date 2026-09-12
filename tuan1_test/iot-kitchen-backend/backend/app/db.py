"""Truy cập TimescaleDB bằng asyncpg (SQL thuần, dễ giải thích trong báo cáo)."""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from .config import Settings
from .schemas import TelemetryIn

log = logging.getLogger("db")

CONFIG_COLUMNS = (
    "pollution_threshold", "temp_threshold", "dwell_time_seconds",
    "alert_pollution_threshold", "alert_temp_threshold",
)


class Database:
    def __init__(self, settings: Settings):
        self.s = settings
        self.pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Kết nối (thử lại với backoff vì DB có thể khởi động chậm hơn Backend)
    # ------------------------------------------------------------------
    async def connect(self, max_attempts: int = 30) -> None:
        delay = 1.0
        for attempt in range(1, max_attempts + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    host=self.s.pg_host, port=self.s.pg_port, user=self.s.pg_user,
                    password=self.s.pg_password, database=self.s.pg_database,
                    min_size=1, max_size=5, init=self._init_connection,
                )
                log.info("Đã kết nối TimescaleDB %s:%s/%s", self.s.pg_host, self.s.pg_port, self.s.pg_database)
                return
            except Exception as exc:  # DB chưa sẵn sàng, sai mật khẩu, sai host...
                log.warning("Chưa kết nối được DB (lần %d/%d): %s - thử lại sau %.0fs",
                            attempt, max_attempts, exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)
        raise RuntimeError("Không kết nối được TimescaleDB - kiểm tra container timescaledb và file .env")

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        # Tự chuyển JSONB <-> dict Python
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ping(self) -> None:
        await self.pool.fetchval("SELECT 1")

    # ------------------------------------------------------------------
    # TELEMETRY
    # ------------------------------------------------------------------
    async def insert_telemetry(self, device_id: str, sample_time: datetime,
                               received_at: datetime, t: TelemetryIn) -> None:
        await self.pool.execute(
            """
            INSERT INTO telemetry (time, received_at, device_id, seq, temperature, humidity,
                                   pollution_percent, rs_ro_ratio, fan_state, mode, network_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            sample_time, received_at, device_id, t.seq, t.temperature, t.humidity,
            t.pollution_percent, t.rs_ro_ratio, t.fan_state, t.mode, t.network_status,
        )

    async def latest_telemetry(self, device_id: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            SELECT time AS timestamp, received_at, seq, temperature, humidity, pollution_percent,
                   rs_ro_ratio, fan_state, mode, network_status
            FROM telemetry
            WHERE device_id = $1
            ORDER BY time DESC
            LIMIT 1
            """,
            device_id,
        )

    async def history(self, device_id: str, start: datetime, end: datetime, limit: int, offset: int,
                      order: str, bucket_seconds: int | None) -> tuple[int, list[asyncpg.Record]]:
        direction = "DESC" if order == "desc" else "ASC"   # chỉ nhận 2 giá trị cố định -> an toàn SQL injection

        if bucket_seconds is None:
            where = "WHERE device_id = $1 AND time >= $2 AND time <= $3"
            total = await self.pool.fetchval(f"SELECT count(*) FROM telemetry {where}", device_id, start, end)
            rows = await self.pool.fetch(
                f"""
                SELECT time AS timestamp, received_at, seq, temperature, humidity, pollution_percent,
                       rs_ro_ratio, fan_state, mode, network_status
                FROM telemetry {where}
                ORDER BY time {direction}
                LIMIT $4 OFFSET $5
                """,
                device_id, start, end, limit, offset,
            )
            return total, rows

        # Gộp dữ liệu theo khung thời gian bằng time_bucket() của TimescaleDB (dùng cho biểu đồ 1h/6h/24h)
        bucket = timedelta(seconds=bucket_seconds)
        where = "WHERE device_id = $2 AND time >= $3 AND time <= $4"
        total = await self.pool.fetchval(
            f"SELECT count(*) FROM (SELECT 1 FROM telemetry {where} GROUP BY time_bucket($1::interval, time)) b",
            bucket, device_id, start, end,
        )
        rows = await self.pool.fetch(
            f"""
            SELECT time_bucket($1::interval, time)                  AS timestamp,
                   round(avg(temperature)::numeric, 2)::float8        AS temperature,
                   round(avg(humidity)::numeric, 2)::float8           AS humidity,
                   round(avg(pollution_percent)::numeric, 2)::float8  AS pollution_percent,
                   round(avg(rs_ro_ratio)::numeric, 3)::float8        AS rs_ro_ratio,
                   last(fan_state, time)                              AS fan_state,
                   last(mode, time)                                   AS mode
            FROM telemetry {where}
            GROUP BY 1
            ORDER BY 1 {direction}
            LIMIT $5 OFFSET $6
            """,
            bucket, device_id, start, end, limit, offset,
        )
        return total, rows

    # ------------------------------------------------------------------
    # CHẨN ĐOÁN CHẤT LƯỢNG ĐƯỜNG TRUYỀN (phục vụ Bài test 1 và Bài test 4)
    # ------------------------------------------------------------------
    async def diagnostics(self, device_id: str, minutes: int, expected_interval_s: float) -> dict[str, Any]:
        window = timedelta(minutes=minutes)
        row = await self.pool.fetchrow(
            """
            WITH w AS (
                SELECT time, received_at, seq, temperature, humidity, pollution_percent, rs_ro_ratio,
                       lag(seq)  OVER (ORDER BY time) AS prev_seq,
                       lag(time) OVER (ORDER BY time) AS prev_time
                FROM telemetry
                WHERE device_id = $1 AND time > now() - $2::interval
            )
            SELECT
                count(*)                                                        AS samples,
                count(seq)                                                      AS with_seq,
                min(seq)                                                        AS seq_min,
                max(seq)                                                        AS seq_max,
                -- mất gói theo số thứ tự: cộng dồn phần bị nhảy cóc
                coalesce(sum(seq - prev_seq - 1)
                         FILTER (WHERE seq > prev_seq + 1), 0)                  AS seq_missing,
                count(*) FILTER (WHERE seq < prev_seq)                          AS reboots,
                -- ước lượng theo khoảng trống thời gian (dùng khi firmware chưa gửi seq)
                count(*) FILTER (WHERE prev_time IS NOT NULL)                   AS intervals,
                coalesce(sum(floor(extract(epoch FROM time - prev_time) / $3) - 1)
                         FILTER (WHERE extract(epoch FROM time - prev_time) > $3 * 1.5), 0) AS gap_missing,
                -- độ trễ ghi dữ liệu
                avg (extract(epoch FROM received_at - time) * 1000)             AS lat_avg,
                max (extract(epoch FROM received_at - time) * 1000)             AS lat_max,
                percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY extract(epoch FROM received_at - time) * 1000)     AS lat_p95,
                -- mốc thời gian có phần mili-giây khác 000 hay không
                count(*) FILTER (WHERE (extract(epoch FROM time) * 1000)::bigint % 1000 <> 0) AS with_ms,
                -- số bản ghi thiếu từng số đo
                count(*) FILTER (WHERE temperature IS NULL)                     AS null_temperature,
                count(*) FILTER (WHERE humidity IS NULL)                        AS null_humidity,
                count(*) FILTER (WHERE pollution_percent IS NULL)               AS null_pollution_percent,
                count(*) FILTER (WHERE rs_ro_ratio IS NULL)                     AS null_rs_ro_ratio
            FROM w
            """,
            device_id, window, expected_interval_s,
        )
        return dict(row)

    # ------------------------------------------------------------------
    # SYSTEM EVENTS
    # ------------------------------------------------------------------
    async def log_event(self, device_id: str, event_type: str, message: str, severity: str = "INFO",
                        source: str = "backend", details: dict[str, Any] | None = None) -> None:
        """Ghi nhật ký. Lỗi ghi log KHÔNG được làm hỏng luồng chính -> chỉ ghi cảnh báo."""
        try:
            await self.pool.execute(
                """
                INSERT INTO system_events (device_id, event_type, severity, source, message, details)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                device_id, event_type, severity, source, message, details,
            )
        except Exception as exc:
            log.error("Không ghi được system_event %s: %s", event_type, exc)

    async def recent_events(self, device_id: str, limit: int, event_type: str | None) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT id, time, device_id, event_type, severity, source, message, details
            FROM system_events
            WHERE device_id = $1 AND ($2::text IS NULL OR event_type = $2)
            ORDER BY time DESC, id DESC
            LIMIT $3
            """,
            device_id, event_type, limit,
        )

    # ------------------------------------------------------------------
    # DEVICE CONFIG
    # ------------------------------------------------------------------
    async def ensure_config(self, device_id: str) -> None:
        await self.pool.execute(
            "INSERT INTO device_config (device_id) VALUES ($1) ON CONFLICT (device_id) DO NOTHING", device_id)

    async def get_config(self, device_id: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM device_config WHERE device_id = $1", device_id)

    async def update_config(self, device_id: str, fields: dict[str, Any], updated_by: str) -> asyncpg.Record:
        await self.ensure_config(device_id)
        columns = [c for c in CONFIG_COLUMNS if c in fields]   # chỉ cho phép cột trong danh sách trắng
        assignments = ", ".join(f"{col} = ${i}" for i, col in enumerate(columns, start=3))
        return await self.pool.fetchrow(
            f"""
            UPDATE device_config
            SET {assignments}, updated_at = now(), updated_by = $2
            WHERE device_id = $1
            RETURNING *
            """,
            device_id, updated_by, *[fields[c] for c in columns],
        )
