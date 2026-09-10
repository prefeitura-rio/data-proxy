CREATE OR REPLACE VIEW ${schema}.${view_name} AS
SELECT ${select_cols} FROM ${schema}.${fn_name}()
