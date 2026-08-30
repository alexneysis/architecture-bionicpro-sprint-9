-- 1. Kafka source

CREATE TABLE IF NOT EXISTS bionicpro.crm_clients_kafka
(
    message String
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'crm.public.clients',
    kafka_group_name = 'clickhouse-crm-consumer',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1;


-- 2. Текущее состояние CRM

CREATE TABLE IF NOT EXISTS bionicpro.crm_clients_current
(
    user_id String,
    prosthesis_id String,
    full_name String,
    is_deleted UInt8,
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY prosthesis_id;


-- 3. Итоговая витрина

CREATE TABLE IF NOT EXISTS bionicpro.report_mart_cdc
(
    report_date Date,

    user_id String,
    prosthesis_id String,
    full_name String,

    telemetry_events UInt64,
    avg_signal_strength Float64,
    movements UInt64,
    avg_battery_level Float64,

    is_deleted UInt8,
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(report_date)
ORDER BY (
    user_id,
    report_date,
    prosthesis_id
);


-- 4. Kafka -> crm_clients_current

CREATE MATERIALIZED VIEW IF NOT EXISTS
    bionicpro.crm_clients_kafka_mv

TO bionicpro.crm_clients_current

AS
SELECT
    if(
        op = 'd',
        JSONExtractString(
            JSONExtractRaw(message, 'before'),
            'user_id'
        ),
        JSONExtractString(
            JSONExtractRaw(message, 'after'),
            'user_id'
        )
    ) AS user_id,

    if(
        op = 'd',
        JSONExtractString(
            JSONExtractRaw(message, 'before'),
            'prosthesis_id'
        ),
        JSONExtractString(
            JSONExtractRaw(message, 'after'),
            'prosthesis_id'
        )
    ) AS prosthesis_id,

    if(
        op = 'd',
        JSONExtractString(
            JSONExtractRaw(message, 'before'),
            'full_name'
        ),
        JSONExtractString(
            JSONExtractRaw(message, 'after'),
            'full_name'
        )
    ) AS full_name,

    toUInt8(op = 'd') AS is_deleted,

    toUInt64(
        JSONExtractInt(message, 'ts_ms')
    ) AS version

FROM
(
    SELECT
        message,
        JSONExtractString(message, 'op') AS op

    FROM bionicpro.crm_clients_kafka
)

WHERE op IN ('r', 'c', 'u', 'd');


-- 5. Telemetry -> report mart

CREATE MATERIALIZED VIEW IF NOT EXISTS
    bionicpro.telemetry_to_report_mv

TO bionicpro.report_mart_cdc

AS
SELECT
    t.report_date,

    c.user_id,
    t.prosthesis_id,
    c.full_name,

    t.telemetry_events,
    t.avg_signal_strength,
    t.movements,
    t.avg_battery_level,

    c.is_deleted,

    greatest(
        c.version,
        toUInt64(toUnixTimestamp(t.report_date)) * 1000
    ) AS version

FROM bionicpro.stg_telemetry_daily AS t

INNER JOIN
(
    SELECT
        user_id,
        prosthesis_id,
        full_name,
        is_deleted,
        version

    FROM bionicpro.crm_clients_current FINAL

    WHERE is_deleted = 0
) AS c

ON c.prosthesis_id = t.prosthesis_id;


-- 6. CRM updates -> report mart

CREATE MATERIALIZED VIEW IF NOT EXISTS
    bionicpro.crm_to_report_mv

TO bionicpro.report_mart_cdc

AS
SELECT
    t.report_date,

    c.user_id,
    c.prosthesis_id,
    c.full_name,

    t.telemetry_events,
    t.avg_signal_strength,
    t.movements,
    t.avg_battery_level,

    c.is_deleted,
    c.version

FROM bionicpro.crm_clients_current AS c

INNER JOIN bionicpro.stg_telemetry_daily AS t
    ON t.prosthesis_id = c.prosthesis_id;