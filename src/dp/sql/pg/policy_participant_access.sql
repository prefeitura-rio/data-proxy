ALTER TABLE ${schema}.${table} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS participant_access_select ON ${schema}.${table};
DROP POLICY IF EXISTS participant_access_update ON ${schema}.${table};
DROP POLICY IF EXISTS participant_access_delete ON ${schema}.${table};
CREATE POLICY participant_access_select ON ${schema}.${table}
FOR SELECT
USING (
    EXISTS (
        SELECT 1
        FROM rls.get_user_permissions() AS p
        WHERE p.is_super_admin
           OR id_cras = ANY(p.id_cras_list)
           OR id_escola = ANY(p.id_escola_list)
           OR id_cre = ANY(p.id_cre_list)
           OR id_ap = ANY(p.id_ap_list)
           OR id_cas = ANY(p.id_cas_list)
           OR id_clinica_familia = ANY(p.id_clinica_familia_list)
           OR id_equipe_familia = ANY(p.id_equipe_familia_list)
    )
);
CREATE POLICY participant_access_update ON ${schema}.${table}
FOR UPDATE
USING (
    EXISTS (
        SELECT 1
        FROM rls.get_user_permissions() AS p
        WHERE p.is_super_admin
           OR id_cras = ANY(p.id_cras_list)
           OR id_escola = ANY(p.id_escola_list)
           OR id_cre = ANY(p.id_cre_list)
           OR id_ap = ANY(p.id_ap_list)
           OR id_cas = ANY(p.id_cas_list)
           OR id_clinica_familia = ANY(p.id_clinica_familia_list)
           OR id_equipe_familia = ANY(p.id_equipe_familia_list)
    )
);
CREATE POLICY participant_access_delete ON ${schema}.${table}
FOR DELETE
USING (
    EXISTS (
        SELECT 1
        FROM rls.get_user_permissions() AS p
        WHERE p.is_super_admin
           OR id_cras = ANY(p.id_cras_list)
           OR id_escola = ANY(p.id_escola_list)
           OR id_cre = ANY(p.id_cre_list)
           OR id_ap = ANY(p.id_ap_list)
           OR id_cas = ANY(p.id_cas_list)
           OR id_clinica_familia = ANY(p.id_clinica_familia_list)
           OR id_equipe_familia = ANY(p.id_equipe_familia_list)
    )
)
