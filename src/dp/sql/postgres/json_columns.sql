SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND data_type = 'json' ORDER BY column_name
