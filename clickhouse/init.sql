CREATE DATABASE IF NOT EXISTS bionicpro;


CREATE TABLE IF NOT EXISTS bionicpro.stg_crm_clients
(
    user_id String,
    prosthesis_id String,
    full_name String
)
ENGINE = MergeTree
ORDER BY (user_id, prosthesis_id);


CREATE TABLE IF NOT EXISTS bionicpro.stg_telemetry_daily
(
    report_date Date,
    prosthesis_id String,
    telemetry_events UInt64,
    avg_signal_strength Float64,
    movements UInt64,
    avg_battery_level Float64
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(report_date)
ORDER BY (prosthesis_id, report_date);


CREATE TABLE IF NOT EXISTS bionicpro.report_mart
(
    report_date Date,

    user_id String,
    prosthesis_id String,
    full_name String,

    telemetry_events UInt64,
    avg_signal_strength Float64,
    movements UInt64,
    avg_battery_level Float64,

    processed_at DateTime
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(report_date)
ORDER BY (user_id, report_date, prosthesis_id);


CREATE TABLE IF NOT EXISTS bionicpro.etl_state
(
    pipeline String,
    processed_through Date,
    updated_at DateTime
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY pipeline;