SELECT indexdef FROM pg_indexes
WHERE schemaname = '${schema}' AND indexname = 'idx_data_status'
