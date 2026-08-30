CREATE DATABASE IF NOT EXISTS bionicpro;


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


CREATE TABLE IF NOT EXISTS bionicpro.etl_state
(
    pipeline String,
    processed_through Date,
    updated_at DateTime
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY pipeline;