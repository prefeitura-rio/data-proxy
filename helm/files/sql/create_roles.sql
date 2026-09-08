SELECT format('CREATE ROLE %I', :'anon_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'anon_role')\gexec
SELECT format('CREATE ROLE %I', :'user_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user_role')\gexec
SELECT format('CREATE ROLE %I', :'authenticator_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'authenticator_role')\gexec
