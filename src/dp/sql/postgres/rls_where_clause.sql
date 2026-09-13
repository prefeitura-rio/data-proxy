AND EXISTS (
    SELECT 1 FROM ${schema}.access_policy p
    WHERE p.subject = current_setting(${session_var}, true)
      AND p.is_enabled
      AND (p.is_admin OR ${predicate})
)
