INSERT INTO {{ schema }}.visible VALUES ('allowed'), ('denied');
INSERT INTO {{ schema }}.access_policy (subject, unit_type, unit_id)
VALUES ('alice', 'cras', 'allowed');
GRANT USAGE ON SCHEMA {{ schema }} TO "user";
