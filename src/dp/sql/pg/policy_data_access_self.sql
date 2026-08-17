ALTER TABLE ${schema}.${table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS data_access_self ON ${schema}.${table};
CREATE POLICY data_access_self ON ${schema}.${table}
FOR SELECT
USING (
    cpf = COALESCE(
        current_setting('request.jwt.claims', true)::json ->> 'preferred_username',
        ''
    )
)
