CREATE TABLE clients
(
    user_id VARCHAR(100) NOT NULL,
    prosthesis_id VARCHAR(100) NOT NULL,
    full_name VARCHAR(255) NOT NULL,

    PRIMARY KEY (user_id, prosthesis_id)
);


INSERT INTO clients (
    user_id,
    prosthesis_id,
    full_name
)
VALUES
    ('user1', 'prosthesis_1', 'User one'),
    ('john.doe', 'prosthesis_2', 'User two'),
    ('alex.johnson', 'prosthesis_3', 'User three');