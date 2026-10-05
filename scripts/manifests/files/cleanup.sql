DO $$
DECLARE relation record;
BEGIN
  FOR relation IN
    SELECT tablename
    FROM pg_tables
    WHERE schemaname = '{{ schema }}'
      AND tablename NOT IN ('access_policy', 'access_log')
  LOOP
    EXECUTE format('DROP TABLE IF EXISTS %I.%I CASCADE', '{{ schema }}', relation.tablename);
  END LOOP;
  EXECUTE format('DELETE FROM %I.access_policy', '{{ schema }}');
END;
$$;
