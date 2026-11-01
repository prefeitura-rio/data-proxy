
{{- define "data-proxy.dbSecretName" -}}
{{- include "data-proxy.fullname" . }}-cnpg-ops
{{- end }}

{{- define "data-proxy.signerSecretName" -}}
{{- printf "%s-signer" (include "data-proxy.fullname" .) }}
{{- end }}

{{- define "data-proxy.servingSecretName" -}}
{{- printf "%s-cnpg-%s" (include "data-proxy.fullname" .) .Values.auth.servingRole }}
{{- end }}

{{- define "data-proxy.duckdbSecretName" -}}
{{- printf "%s-cnpg-duckdb" (include "data-proxy.fullname" .) }}
{{- end }}

{{- define "data-proxy.pgEnv" -}}
- name: PGHOST
  value: {{ printf "%s-rw" (include "data-proxy.fullname" .) | quote }}
- name: PGPORT
  value: "5432"
- name: PGDATABASE
  value: {{ .Values.cnpg.db.name | quote }}
- name: PGUSER
  value: {{ .Values.cnpg.db.user | quote }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.dbSecretName" . }}
      key: password
{{- end }}

{{- /*
PostgREST sends its queries to PgDog. Its listener needs a read-write session, which PgDog does not give,
so libpq moves on to the second host, the primary.
*/ -}}
{{- define "data-proxy.postgrestDsn" -}}
{{- $role := .Values.auth.servingRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_PASSWORD)@{{ $cluster }}-pgdog:6432,{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.appPgDsn" -}}
{{- $user := .Values.cnpg.db.user -}}
{{- $db   := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgresql://{{ $user }}:$(PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.dbosClusterName" -}}
{{- include "data-proxy.fullname" . -}}
{{- end }}

{{- define "data-proxy.dbosSystemDatabaseUrl" -}}
{{- $user := .Values.cnpg.db.user -}}
{{- $db   := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.dbosClusterName" . -}}
postgresql://{{ $user }}:$(PASSWORD)@{{ $cluster }}-rw.{{ .Release.Namespace }}.svc.cluster.local:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.postgresHome" -}}
/var/lib/postgresql/data
{{- end }}

{{- define "data-proxy.cnpgPodPatch" -}}
{{- $secretsVolume := dict
  "name" "duckdb"
  "persistentVolumeClaim" (dict "claimName" (printf "%s-duckdb" (include "data-proxy.fullname" .)))
-}}
{{- $secretsMount := dict
  "name" "duckdb"
  "mountPath" (printf "%s/.duckdb/stored_secrets" (include "data-proxy.postgresHome" .))
  "subPath" "duckdb/secrets"
  "readOnly" false
-}}
{{- $prepareSecrets := dict
  "name" "prepare-duckdb-secrets"
  "image" .Values.cnpg.image
  "imagePullPolicy" "IfNotPresent"
  "command" (list "install" "-d" "-m" "0700" "/shared/duckdb" "/shared/duckdb/secrets")
  "volumeMounts" (list (dict "name" "duckdb" "mountPath" "/shared"))
-}}
{{- $tmpfsVolume := dict
  "name" "duckdb-tmpfs"
  "emptyDir" (dict "medium" "Memory")
-}}
{{- $tmpfsMount := dict
  "name" "duckdb-tmpfs"
  "mountPath" "/var/lib/postgresql/data/pg_duckdb/temp"
  "readOnly" false
-}}
{{- $postStart := dict
  "postStart" (dict
    "exec" (dict
      "command" (list "sh" "-c" "psql -U postgres -d data-proxy -tAc \"SELECT duckdb.raw_query('LOAD ducklake; LOAD postgres')\" > /dev/null 2>&1 || true")
    )
  )
-}}
{{- $s3Volume := include "data-proxy.s3BootstrapVolume" . | fromYamlArray | first -}}
{{- $waitForS3 := include "data-proxy.s3WaitInitContainer" . | fromYamlArray | first -}}
{{- $operations := list
  (dict "op" "add" "path" "/spec/volumes/-" "value" $secretsVolume)
  (dict "op" "add" "path" "/spec/volumes/-" "value" $s3Volume)
  (dict "op" "add" "path" "/spec/volumes/-" "value" $tmpfsVolume)
  (dict "op" "add" "path" "/spec/initContainers/-" "value" $waitForS3)
  (dict "op" "add" "path" "/spec/initContainers/-" "value" $prepareSecrets)
  (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" $secretsMount)
  (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" $tmpfsMount)
  (dict "op" "add" "path" "/spec/containers/0/lifecycle" "value" $postStart)
-}}
{{- if .Values.gcp.existingSecret }}
{{- $operations = append $operations (dict "op" "add" "path" "/spec/volumes/-" "value" (include "data-proxy.gcpVolume" . | fromYamlArray | first)) -}}
{{- $operations = append $operations (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" (include "data-proxy.gcpVolumeMount" . | fromYamlArray | first)) -}}
{{- end }}
{{- $operations | toJson -}}
{{- end }}

{{- define "data-proxy.duckdbThreads" -}}
{{- $threads := .Values.cnpg.duckdb.threads | default 2 | int -}}
{{- if lt $threads 1 -}}
  {{- $threads = 1 -}}
{{- end -}}
{{- $threads | quote -}}
{{- end }}

{{- define "data-proxy.cnpgParameters" -}}
{{- $defaults := dict
  "shared_buffers" "1GB"
  "work_mem" "64MB"
  "maintenance_work_mem" "512MB"
  "effective_cache_size" "5GB"
  "max_parallel_workers_per_gather" "4"
  "duckdb.max_temp_directory_size" "2GB"
  "random_page_cost" "1.1"
  "effective_io_concurrency" "200"
  "max_connections" "200"
  "max_wal_size" "4GB"
  "max_slot_wal_keep_size" "8GB"
  "min_wal_size" "1GB"
  "wal_compression" "on"
  "checkpoint_timeout" "15min"
  "checkpoint_completion_target" "0.9"
-}}
{{- if .Values.cnpg.postgresql.synchronous -}}
{{- $_ := set $defaults "synchronous_commit" "local" -}}
{{- end -}}
{{- $params := mergeOverwrite $defaults (default (dict) .Values.cnpg.postgresql.parameters) -}}
{{- toYaml $params -}}
{{- end }}
