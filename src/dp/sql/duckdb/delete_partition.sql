DELETE FROM pg.${schema}.${table_name}
WHERE ${partition_column} = '${partition_value}'
