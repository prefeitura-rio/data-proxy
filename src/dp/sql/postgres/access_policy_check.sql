ALTER TABLE ${schema}.${table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS access_policy_scoped ON ${schema}.${table};
CREATE POLICY access_policy_scoped ON ${schema}.${table}
USING (
    ${scope}
    AND EXISTS (
        SELECT 1 FROM ${schema}.access_policy AS p
        WHERE p.subject = current_setting(${session_var}, true)
          AND p.is_enabled
          AND (p.is_admin OR (${predicate}))
    )
)
