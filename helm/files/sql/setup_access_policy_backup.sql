GRANT SELECT ON :"schema".access_policy TO backup;
DROP POLICY IF EXISTS backup_read ON :"schema".access_policy;
CREATE POLICY backup_read ON :"schema".access_policy
    FOR SELECT TO backup
    USING (true)
