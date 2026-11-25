DO $$
DECLARE relation record;
BEGIN
  FOR relation IN
    SELECT tablename
    FROM pg_tables
    WHERE schemaname = '{{ schema }}'
  LOOP
    EXECUTE format('DROP TABLE IF EXISTS %I.%I CASCADE', '{{ schema }}', relation.tablename);
  END LOOP;
  DELETE FROM access.policy;
END;
$$;

DO $$
BEGIN
  IF to_regclass('console.schemas') IS NOT NULL THEN
    DELETE FROM console.tables WHERE schema = '{{ new_schema }}';
    DELETE FROM console.schemas WHERE name = '{{ new_schema }}';
  END IF;
END;
$$;

DROP SCHEMA IF EXISTS {{ new_schema }} CASCADE;

DO $$
BEGIN
  IF to_regclass('data_proxy.state') IS NOT NULL THEN
    DELETE FROM data_proxy.state;
  END IF;
  IF to_regclass('data_proxy.errors') IS NOT NULL THEN
    DELETE FROM data_proxy.errors;
  END IF;
  IF to_regclass('data_proxy.scope') IS NOT NULL THEN
    DELETE FROM data_proxy.scope;
  END IF;
  IF to_regclass('dbos.workflow_status') IS NOT NULL THEN
    UPDATE dbos.workflow_status
    SET status = 'CANCELLED', error = 'Cancelled before test run', deduplication_id = NULL
    WHERE application_name = 'data-proxy-sync' AND status IN ('PENDING', 'ENQUEUED', 'DELAYED');
  END IF;
END;
$$;
