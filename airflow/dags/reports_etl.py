import os
import logging
from datetime import datetime, timedelta, timezone

import clickhouse_connect
import pendulum
import psycopg2

from airflow.sdk import dag, task, get_current_context


logger = logging.getLogger(__name__)


CRM_DSN = os.getenv(
    "CRM_DSN",
    "postgresql://crm:crm@crm-db:5432/crm",
)

TELEMETRY_DSN = os.getenv(
    "TELEMETRY_DSN",
    "postgresql://telemetry:telemetry@telemetry-db:5432/telemetry",
)

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "clickhouse")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "clickhouse")


def get_clickhouse():
    logger.info(
        "[get_clickhouse] Connecting to ClickHouse: host=%s port=%s database=bionicpro",
        CLICKHOUSE_HOST,
        CLICKHOUSE_PORT,
    )

    try:
        client = clickhouse_connect.get_client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            username=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database="bionicpro",
        )

        logger.info("[get_clickhouse] ClickHouse client created successfully")
        return client

    except Exception:
        logger.exception("[get_clickhouse] ERROR connecting to ClickHouse")
        raise


def get_target_date():
    logger.info("[get_target_date] Getting Airflow context")

    try:
        context = get_current_context()

        logger.info(
            "[get_target_date] data_interval_start=%s data_interval_end=%s",
            context.get("data_interval_start"),
            context.get("data_interval_end"),
        )

        target_date = (
            context["data_interval_end"]
            .in_timezone("UTC")
            .date()
            - timedelta(days=2)
        )

        logger.info("[get_target_date] target_date=%s", target_date)

        return target_date

    except Exception:
        logger.exception("[get_target_date] ERROR getting target date")
        raise


@dag(
    dag_id="build_user_reports",
    schedule="10 0 * * *",  # schedule="10 0 * * *",   */2 * * * *
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["reports", "etl"],
)
def reports_etl():

    @task
    def extract_crm():
        logger.info("[extract_crm] START")

        try:
            logger.info("[extract_crm] Connecting to CRM PostgreSQL")

            with psycopg2.connect(CRM_DSN) as connection:
                logger.info("[extract_crm] Connected to CRM PostgreSQL")

                with connection.cursor() as cursor:
                    logger.info("[extract_crm] Executing clients query")

                    cursor.execute(
                        """
                        SELECT
                            user_id,
                            prosthesis_id,
                            full_name
                        FROM clients
                        """
                    )

                    rows = cursor.fetchall()

                    logger.info(
                        "[extract_crm] Rows fetched: %s",
                        len(rows),
                    )

                    if rows:
                        logger.info(
                            "[extract_crm] First row: %s",
                            rows[0],
                        )

            logger.info("[extract_crm] Connecting to ClickHouse")
            clickhouse = get_clickhouse()

            logger.info("[extract_crm] Truncating stg_crm_clients")

            clickhouse.command(
                "TRUNCATE TABLE bionicpro.stg_crm_clients"
            )

            logger.info("[extract_crm] stg_crm_clients truncated")

            if rows:
                logger.info(
                    "[extract_crm] Inserting %s rows into stg_crm_clients",
                    len(rows),
                )

                clickhouse.insert(
                    "stg_crm_clients",
                    rows,
                    column_names=[
                        "user_id",
                        "prosthesis_id",
                        "full_name",
                    ],
                )

                logger.info("[extract_crm] Insert completed")
            else:
                logger.warning("[extract_crm] No rows to insert")

            logger.info("[extract_crm] END")

        except Exception:
            logger.exception("[extract_crm] TASK FAILED")
            raise

    @task
    def extract_telemetry():
        logger.info("[extract_telemetry] START")

        try:
            target_date = get_target_date()
            next_date = target_date + timedelta(days=1)

            logger.info(
                "[extract_telemetry] Date range: %s <= captured_at < %s",
                target_date,
                next_date,
            )

            logger.info(
                "[extract_telemetry] Connecting to telemetry PostgreSQL"
            )

            with psycopg2.connect(TELEMETRY_DSN) as connection:
                logger.info(
                    "[extract_telemetry] Connected to telemetry PostgreSQL"
                )

                with connection.cursor() as cursor:
                    logger.info(
                        "[extract_telemetry] Executing telemetry aggregation query"
                    )

                    cursor.execute(
                        """
                        SELECT
                            prosthesis_id,
                            COUNT(*) AS telemetry_events,
                            AVG(signal_strength),
                            SUM(movement_detected),
                            AVG(battery_level)
                        FROM telemetry
                        WHERE captured_at >= %s
                          AND captured_at < %s
                        GROUP BY prosthesis_id
                        """,
                        (
                            target_date,
                            next_date,
                        ),
                    )

                    source_rows = cursor.fetchall()

                    logger.info(
                        "[extract_telemetry] Aggregated rows fetched: %s",
                        len(source_rows),
                    )

                    if source_rows:
                        logger.info(
                            "[extract_telemetry] First source row: %s",
                            source_rows[0],
                        )

            logger.info(
                "[extract_telemetry] Converting PostgreSQL rows"
            )

            rows = [
                [
                    target_date,
                    prosthesis_id,
                    int(events),
                    float(signal_strength),
                    int(movements),
                    float(battery),
                ]
                for (
                    prosthesis_id,
                    events,
                    signal_strength,
                    movements,
                    battery,
                ) in source_rows
            ]

            logger.info(
                "[extract_telemetry] Converted rows: %s",
                len(rows),
            )

            if rows:
                logger.info(
                    "[extract_telemetry] First converted row: %s",
                    rows[0],
                )

            clickhouse = get_clickhouse()

            logger.info(
                "[extract_telemetry] Deleting old staging data for report_date=%s",
                target_date,
            )

            clickhouse.command(
                f"""
                ALTER TABLE bionicpro.stg_telemetry_daily
                DELETE WHERE report_date = toDate('{target_date}')
                SETTINGS mutations_sync = 1
                """
            )

            logger.info(
                "[extract_telemetry] Old staging data deleted"
            )

            if rows:
                logger.info(
                    "[extract_telemetry] Inserting %s rows into stg_telemetry_daily",
                    len(rows),
                )

                clickhouse.insert(
                    "stg_telemetry_daily",
                    rows,
                    column_names=[
                        "report_date",
                        "prosthesis_id",
                        "telemetry_events",
                        "avg_signal_strength",
                        "movements",
                        "avg_battery_level",
                    ],
                )

                logger.info(
                    "[extract_telemetry] Insert completed"
                )
            else:
                logger.warning(
                    "[extract_telemetry] No rows to insert"
                )

            logger.info("[extract_telemetry] END")

        except Exception:
            logger.exception("[extract_telemetry] TASK FAILED")
            raise

    @task
    def build_report_mart():
        logger.info("[build_report_mart] START")

        try:
            target_date = get_target_date()

            logger.info(
                "[build_report_mart] target_date=%s",
                target_date,
            )

            clickhouse = get_clickhouse()

            logger.info(
                "[build_report_mart] Deleting existing rows for %s",
                target_date,
            )

            clickhouse.command(
                f"""
                ALTER TABLE bionicpro.report_mart
                DELETE WHERE report_date = toDate('{target_date}')
                SETTINGS mutations_sync = 1
                """
            )

            logger.info(
                "[build_report_mart] Existing rows deleted"
            )

            logger.info(
                "[build_report_mart] Inserting report mart rows"
            )

            clickhouse.command(
                f"""
                INSERT INTO bionicpro.report_mart
                SELECT
                    t.report_date,
                    c.user_id,
                    c.prosthesis_id,
                    c.full_name,

                    t.telemetry_events,
                    t.avg_signal_strength,
                    t.movements,
                    t.avg_battery_level,

                    now()
                FROM bionicpro.stg_telemetry_daily AS t
                INNER JOIN bionicpro.stg_crm_clients AS c
                    ON c.prosthesis_id = t.prosthesis_id

                WHERE t.report_date = toDate('{target_date}')
                """
            )

            logger.info(
                "[build_report_mart] Insert completed"
            )

            logger.info("[build_report_mart] END")

        except Exception:
            logger.exception("[build_report_mart] TASK FAILED")
            raise

    @task
    def mark_processed():
        logger.info("[mark_processed] START")

        try:
            target_date = get_target_date()

            logger.info(
                "[mark_processed] target_date=%s",
                target_date,
            )

            clickhouse = get_clickhouse()

            updated_at = datetime.now(timezone.utc)

            logger.info(
                "[mark_processed] Writing etl_state: "
                "pipeline=daily_reports processed_through=%s updated_at=%s",
                target_date,
                updated_at,
            )

            clickhouse.insert(
                "etl_state",
                [
                    [
                        "daily_reports",
                        target_date,
                        updated_at,
                    ]
                ],
                column_names=[
                    "pipeline",
                    "processed_through",
                    "updated_at",
                ],
            )

            logger.info(
                "[mark_processed] etl_state insert completed"
            )

            logger.info("[mark_processed] END")

        except Exception:
            logger.exception("[mark_processed] TASK FAILED")
            raise

    crm = extract_crm()
    telemetry = extract_telemetry()

    mart = build_report_mart()

    [crm, telemetry] >> mart >> mark_processed()
    # fake_1() >> fake_2()


reports_etl()
