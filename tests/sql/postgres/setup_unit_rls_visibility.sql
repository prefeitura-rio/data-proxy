INSERT INTO ${schema}.visible VALUES ('allowed'), ('disabled'), ('denied');
INSERT INTO ${schema}.access_policy (subject, is_enabled, unit_type, unit_id)
VALUES ('alice', true, 'cras', 'allowed'), ('alice', false, 'cras', 'disabled');
GRANT USAGE ON SCHEMA ${schema} TO "user";
