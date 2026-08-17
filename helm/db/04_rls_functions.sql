-- This function returns the row-level-security permissions of the requesting
-- JWT user, read from endpoint_data_access by preferred_username (CPF).
-- SECURITY DEFINER: the owner (the sync superuser) bypasses the RLS on
-- endpoint_data_access, so the function sees every access row without
-- exposing them through PostgREST.
CREATE OR REPLACE FUNCTION rls.get_user_permissions()
RETURNS TABLE (
    is_super_admin boolean,
    is_admin boolean,
    id_cras_list text[],
    id_escola_list text[],
    id_cre_list text[],
    id_ap_list text[],
    id_cas_list text[],
    id_clinica_familia_list text[],
    id_equipe_familia_list text[]
) AS $$
DECLARE
    username text;
    empty_list text[] := '{}'::text[];
BEGIN
    username := current_setting('request.jwt.claims', true)::json ->> 'preferred_username';

    IF username IS NULL OR username = '' THEN
        RETURN QUERY SELECT false, false,
            empty_list, empty_list, empty_list, empty_list,
            empty_list, empty_list, empty_list;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        COALESCE(da.is_super_admin, false),
        COALESCE(da.is_admin, false),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_cras_list) AS elem),
            empty_list
        ),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_escola_list) AS elem),
            empty_list
        ),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_cre_list) AS elem),
            empty_list
        ),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_ap_list) AS elem),
            empty_list
        ),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_cas_list) AS elem),
            empty_list
        ),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_clinica_familia_list) AS elem),
            empty_list
        ),
        COALESCE(
            (SELECT array_agg(elem ->> 'id')
             FROM json_array_elements(da.id_equipe_familia_list) AS elem),
            empty_list
        )
    FROM app_pequenos_cariocas.endpoint_data_access AS da
    WHERE da.cpf = username;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = rls, pg_catalog;

REVOKE ALL ON FUNCTION rls.get_user_permissions() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rls.get_user_permissions() TO web_user;
GRANT EXECUTE ON FUNCTION rls.get_user_permissions() TO web_anon;
