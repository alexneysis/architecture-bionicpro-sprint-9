CREATE TABLE telemetry
(
    id BIGSERIAL PRIMARY KEY,
    prosthesis_id VARCHAR(100) NOT NULL,
    captured_at TIMESTAMP NOT NULL,
    signal_strength DOUBLE PRECISION NOT NULL,
    battery_level DOUBLE PRECISION NOT NULL,
    movement_detected INTEGER NOT NULL DEFAULT 0
);


INSERT INTO telemetry (
    prosthesis_id,
    captured_at,
    signal_strength,
    battery_level,
    movement_detected
)
VALUES
(
    'prosthesis_1',
    NOW() - INTERVAL '1 day',
    0.82,
    85,
    1
),
(
    'prosthesis_2',
    NOW() - INTERVAL '1 day' + INTERVAL '1 hour',
    0.91,
    82,
    1
),
(
    'prosthesis_3',
    NOW() - INTERVAL '1 day',
    0.76,
    76,
    1
);