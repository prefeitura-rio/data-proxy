ALTER TABLE ${schema}.${table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS unit_scoped ON ${schema}.${table};
CREATE POLICY unit_scoped ON ${schema}.${table}
USING (
    ${column} = ANY(
        STRING_TO_ARRAY(
            NULLIF(COALESCE(CURRENT_SETTING('app.user_units', true), ''), ''),
            ','
        )
    )
)
