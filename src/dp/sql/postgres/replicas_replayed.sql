{#
{
  "kind": "template",
  "description": "Render the replicas replayed database operation."
}
#}
SELECT COALESCE(
    bool_and(replay_lsn >= %s::pg_lsn),
    true
)
FROM pg_stat_replication
WHERE state = 'streaming';
