{#
{
  "kind": "template",
  "description": "Create the rbac schema, the table of roles, and the functions that answer whether a request may use a feature, then load the rules of the rbac.toml file. The load validates the file, computes the permissions that each role inherits with a recursive union, replaces the stored roles, and writes the known permissions into rbac.can(), so that a name that no rule defines raises an error. Every load moves the counter in rbac.revision, which the access policy triggers move too, so that the proxy learns that what grants access changed. Run it in one transaction. The load turns JIT off for its transaction, because the compile time of JIT costs far more than the queries, which read a handful of rows.",
  "inputs": {
    "rules": "The parsed rbac.toml file: permissions grouped by object, and the roles with their inherits and permissions.",
    "anonymous_role": "SQL-safe identifier of the role of a request without a token.",
    "source_hash": "SHA-256 of the text of the rbac.toml file, in hexadecimal. It is stored in the comment of rbac.roles."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS rbac;

-- A row, not a sequence: a standby replays a sequence only every 32 values, so it would miss changes.
CREATE TABLE IF NOT EXISTS rbac.revision (
    singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    value bigint NOT NULL DEFAULT 0
);
INSERT INTO rbac.revision DEFAULT VALUES ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS rbac.roles (
    name text PRIMARY KEY,
    permissions text[] NOT NULL,
    wrapped text NOT NULL
);

CREATE OR REPLACE FUNCTION rbac.unknown(feature text) RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RAISE EXCEPTION 'Unknown permission: %', feature USING ERRCODE = 'PT500';
END
$$;

CREATE OR REPLACE FUNCTION rbac.mine(schema_name text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT coalesce(
        schema_name <> ''
        AND strpos(schema_name, ',') = 0
        AND (
            strpos(coalesce(current_setting('rbac.schemas', TRUE), ''), ',' || schema_name || ',') > 0
            -- The element * stands for every schema. rbac.mine('*') asks whether the claim has it.
            OR strpos(coalesce(current_setting('rbac.schemas', TRUE), ''), ',*,') > 0
        ),
        FALSE
    )
$$;

CREATE OR REPLACE FUNCTION rbac.load() RETURNS void
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT
        set_config(
            'rbac.permissions',
            coalesce((SELECT wrapped FROM rbac.roles WHERE name = current_setting('app.claim_role', TRUE)), ''),
            TRUE
        ),
        set_config(
            'rbac.schemas',
            CASE
                WHEN coalesce(current_setting('app.claim_schemas', TRUE), '') = '' THEN ''
                ELSE ',' || current_setting('app.claim_schemas', TRUE) || ','
            END,
            TRUE
        )
$$;

REVOKE ALL ON FUNCTION rbac.unknown(text), rbac.mine(text), rbac.load() FROM PUBLIC;

DO $rbac_load$
DECLARE
    rules jsonb := '{{ rules | tojson | replace("$", "\\u0024") | replace("'", "\\u0027") }}'::jsonb;
    source_hash text := '{{ source_hash | replace("'", "''") }}';
    known text;
    problem text;
    role_name text;
BEGIN
    SET LOCAL jit = off;

    CREATE TEMP TABLE rbac_in_roles AS
    SELECT
        r.name,
        coalesce(r.inherits, '{}') AS inherits,
        coalesce(r.permissions, '{}') AS permissions
    FROM jsonb_to_recordset(rules -> 'roles') AS r (name text, inherits text[], permissions text[]);

    CREATE TEMP TABLE rbac_in_known AS
    SELECT action.key || '_' || subject.key AS permission
    FROM jsonb_each(rules -> 'permissions') AS subject
    CROSS JOIN LATERAL jsonb_each(subject.value) AS action
    WHERE action.key <> 'description';

    SELECT name INTO problem FROM rbac_in_roles GROUP BY name HAVING count(*) > 1 LIMIT 1;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'rbac: the role % is defined twice', problem;
    END IF;

    SELECT r.name || ' inherits ' || p INTO problem
    FROM rbac_in_roles AS r CROSS JOIN LATERAL unnest(r.inherits) AS p
    WHERE p NOT IN (SELECT name FROM rbac_in_roles)
    LIMIT 1;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'rbac: unknown role in inherits: %', problem;
    END IF;

    SELECT r.name || ' holds ' || p INTO problem
    FROM rbac_in_roles AS r CROSS JOIN LATERAL unnest(r.permissions) AS p
    WHERE p NOT IN (SELECT permission FROM rbac_in_known)
    LIMIT 1;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'rbac: unknown permission in a role: %', problem;
    END IF;

    WITH RECURSIVE reach (role, ancestor) AS (
        SELECT r.name, p FROM rbac_in_roles AS r CROSS JOIN LATERAL unnest(r.inherits) AS p
        UNION
        SELECT reach.role, p
        FROM reach
        INNER JOIN rbac_in_roles AS r ON r.name = reach.ancestor
        CROSS JOIN LATERAL unnest(r.inherits) AS p
    )
    SELECT role INTO problem FROM reach WHERE role = ancestor LIMIT 1;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'rbac: the inheritance of the role % has a cycle', problem;
    END IF;

    WITH RECURSIVE closure (role, ancestor) AS (
        SELECT name, name FROM rbac_in_roles
        UNION
        SELECT closure.role, p
        FROM closure
        INNER JOIN rbac_in_roles AS r ON r.name = closure.ancestor
        CROSS JOIN LATERAL unnest(r.inherits) AS p
    ),

    effective AS (
        SELECT closure.role AS name, array_agg(DISTINCT held COLLATE "C" ORDER BY held COLLATE "C") AS permissions
        FROM closure
        INNER JOIN rbac_in_roles AS r ON r.name = closure.ancestor
        CROSS JOIN LATERAL unnest(r.permissions) AS held
        GROUP BY closure.role
    )

    INSERT INTO rbac.roles (name, permissions, wrapped)
    SELECT
        r.name,
        coalesce(e.permissions, '{}'),
        CASE WHEN e.permissions IS NULL THEN '' ELSE ',' || array_to_string(e.permissions, ',') || ',' END
    FROM rbac_in_roles AS r
    LEFT JOIN effective AS e ON r.name = e.name
    ON CONFLICT (name) DO UPDATE
    SET permissions = excluded.permissions, wrapped = excluded.wrapped;

    DELETE FROM rbac.roles WHERE name NOT IN (SELECT name FROM rbac_in_roles);

    SELECT ',' || string_agg(permission, ',' ORDER BY permission COLLATE "C") || ',' INTO known FROM rbac_in_known;

    EXECUTE format($rbac_can$
        CREATE OR REPLACE FUNCTION rbac.can(feature text) RETURNS boolean
        LANGUAGE sql STABLE PARALLEL SAFE AS $rbac_body$
            SELECT CASE
                WHEN feature IS NULL OR feature = '' OR strpos(feature, ',') > 0 THEN rbac.unknown(feature)
                WHEN strpos(coalesce(current_setting('rbac.permissions', TRUE), ''), ',' || feature || ',') > 0 THEN TRUE
                WHEN strpos(%L, ',' || feature || ',') > 0 THEN FALSE
                ELSE rbac.unknown(feature)
            END
        $rbac_body$
    $rbac_can$, known);
    REVOKE ALL ON FUNCTION rbac.can(text) FROM PUBLIC;

    FOR role_name IN SELECT name FROM rbac_in_roles LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA rbac TO %I', role_name);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION rbac.load(), rbac.can(text), rbac.mine(text), rbac.unknown(text) TO %I',
            role_name
        );
    END LOOP;

    EXECUTE format('COMMENT ON TABLE rbac.roles IS %L', 'sha256:' || source_hash);

    DROP TABLE rbac_in_roles, rbac_in_known;
END
$rbac_load$;

UPDATE rbac.revision SET value = value + 1;

GRANT USAGE ON SCHEMA rbac TO {{ anonymous_role }};
GRANT EXECUTE ON FUNCTION rbac.load(), rbac.can(text), rbac.mine(text), rbac.unknown(text) TO {{ anonymous_role }};
