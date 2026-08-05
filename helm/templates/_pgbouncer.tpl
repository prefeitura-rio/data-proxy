{{/*
This file defines the PgBouncer connection pool configurations as Helm templates.
*/}}
{{- define "data-proxy.pgbouncerRwConfig" -}}
[databases]
{{ .Values.duckdb.db.name }} = host={{ include "data-proxy.masterServiceName" . }} port=5432 dbname={{ .Values.duckdb.db.name }}

[pgbouncer]
listen_port = 5432
listen_addr = *
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = session
max_client_conn = {{ .Values.ha.pgbouncer.rw.maxClientConn }}
default_pool_size = {{ .Values.ha.pgbouncer.rw.defaultPoolSize }}
server_reset_query = DISCARD ALL
{{- end }}

{{- define "data-proxy.pgbouncerRoConfig" -}}
[databases]
{{ .Values.duckdb.db.name }} = host={{ include "data-proxy.replicaServiceName" . }} port=5432 dbname={{ .Values.duckdb.db.name }}

[pgbouncer]
listen_port = 5432
listen_addr = *
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = session
max_client_conn = {{ .Values.ha.pgbouncer.ro.maxClientConn }}
default_pool_size = {{ .Values.ha.pgbouncer.ro.defaultPoolSize }}
server_reset_query = DISCARD ALL
{{- end }}

{{- define "data-proxy.pgbouncerUserlist" -}}
"{{ .Values.duckdb.db.user }}" "{{ .Values.duckdb.password }}"
"{{ .Values.auth.authenticatorRole }}" "{{ .Values.duckdb.authenticatorPassword }}"
{{- end }}
