{#
{
  "kind": "template",
  "description": "The routing rule that decides which source serves a request."
}
#}
-- noqa: disable=PRS,LT05

-- The routing rule. DuckLake runs first, then each fallback in order.
-- A table that has no published data and no fallback is not found (PT404).
-- The first fallback takes the whole remainder, later ones get FALSE so no rows duplicate.
-- Returns one row per source: (name, use, arg).
-- For DuckLake, arg is the snapshot version (or NULL).
-- For fallback sources, arg is the covered condition (or 'FALSE').
CREATE OR REPLACE FUNCTION {{ schema }}.plan_sources(
  p_table text, p_fallbacks text[], p_pinned boolean
)
RETURNS TABLE (name text, use boolean, arg text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_ducklake text;
  v_remaining text;
  v_use_ducklake boolean;
  v_fallback_name text;
BEGIN
  IF p_pinned THEN
    RETURN QUERY SELECT 'ducklake'::text, true, NULL::text;
    RETURN;
  END IF;

  v_ducklake := {{ schema }}.covered_by_ducklake(p_table);
  v_remaining := {{ schema }}.covered_by_fallback(p_table);

  IF v_ducklake IS NULL THEN
    IF coalesce(array_length(p_fallbacks, 1), 0) = 0 THEN
      RAISE EXCEPTION 'table % has no published data', p_table USING ERRCODE = 'PT404';
    END IF;
    v_use_ducklake := false;
  ELSE
    v_use_ducklake := true;
  END IF;

  RETURN QUERY SELECT 'ducklake'::text, v_use_ducklake, NULL::text;

  FOREACH v_fallback_name IN ARRAY p_fallbacks LOOP
    IF v_remaining IS NOT NULL THEN
      RETURN QUERY SELECT v_fallback_name, true, v_remaining;
      v_remaining := NULL;
    ELSE
      RETURN QUERY SELECT v_fallback_name, false, 'FALSE';
    END IF;
  END LOOP;
END;
$$;
