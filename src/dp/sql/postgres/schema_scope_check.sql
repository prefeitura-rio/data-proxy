ALTER TABLE ${schema}.${table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS schema_scoped ON ${schema}.${table};
CREATE POLICY schema_scoped ON ${schema}.${table}
USING (
    ${scope}
)
