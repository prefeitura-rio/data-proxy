{#
{
  "kind": "template",
  "description": "Run bounded DuckLake snapshot and data-file maintenance.",
  "inputs": {
    "interval": "SQL interval literal used for retention and cleanup.",
    "max_compacted_files": "Maximum compaction outputs per table.",
    "rewrite_delete_threshold": "Minimum deleted fraction for file rewriting."
  }
}
#}
-- noqa: disable=PRS,LT05
CALL ducklake_expire_snapshots('dl', older_than => now() - INTERVAL {{ interval }});
CALL ducklake_merge_adjacent_files('dl', max_compacted_files => {{ max_compacted_files }});
CALL ducklake_rewrite_data_files('dl', delete_threshold => {{ rewrite_delete_threshold }});
CALL ducklake_cleanup_old_files('dl', older_than => now() - INTERVAL {{ interval }});
CALL ducklake_delete_orphaned_files('dl', older_than => now() - INTERVAL {{ interval }});
