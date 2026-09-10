CREATE OR REPLACE FUNCTION duckdb.raw_query(query text)
RETURNS void LANGUAGE plpgsql AS $$$$
BEGIN
    IF query LIKE '%%id_escola IN (''10'')%%' THEN
        RAISE EXCEPTION 'school mapping received cras grant';
    END IF;
    IF query NOT LIKE '%%id_cras IN (''10'')%%' THEN
        RAISE EXCEPTION 'cras mapping did not receive cras grant';
    END IF;
END;
$$$$
