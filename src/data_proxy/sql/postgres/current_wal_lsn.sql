{#
{
  "kind": "template",
  "description": "Render the current wal lsn database operation."
}
#}
SELECT pg_current_wal_lsn();
