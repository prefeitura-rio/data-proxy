{#
{
  "kind": "template",
  "description": "Write one Parquet row whose schema adds, drops, and promotes people columns. Parameters: one output path."
}
#}
COPY (
    SELECT
        1::DOUBLE AS cpf,
        true::BOOLEAN AS active,
        42::INTEGER AS score
) TO ? (FORMAT PARQUET)
