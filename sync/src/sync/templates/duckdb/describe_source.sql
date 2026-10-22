{#
{
  "kind": "template",
  "description": "Describe an ingestion source table schema through DuckDB.",
  "inputs": {
    "source": "Source-generated DuckDB FROM expression."
  }
}
#}
-- noqa: PRS
DESCRIBE SELECT * FROM {{ source }}
