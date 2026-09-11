{{/*
This file defines shared Helm template helpers for names, labels, secrets, and connection strings.
*/}}
{{- define "data-proxy.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "data-proxy.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "data-proxy.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "data-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "data-proxy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "data-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "data-proxy.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- .Values.serviceAccount.name | default (include "data-proxy.fullname" .) }}
{{- else }}
{{- .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "data-proxy.dbSecretName" -}}
{{- if .Values.pgduckdb.existingSecret }}
{{- .Values.pgduckdb.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-db
{{- end }}
{{- end }}

{{- define "data-proxy.backupSecretName" -}}
{{- if .Values.backup.existingSecret }}
{{- .Values.backup.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-backup
{{- end }}
{{- end }}

{{- define "data-proxy.gcsSecretName" -}}
{{- if .Values.gcs.existingSecret }}
{{- .Values.gcs.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-gcs
{{- end }}
{{- end }}

{{- define "data-proxy.valkeySecretName" -}}
{{- if .Values.valkey.auth.existingSecret }}
{{- .Values.valkey.auth.existingSecret }}
{{- else }}
{{- .Release.Name }}-valkey
{{- end }}
{{- end }}

{{- define "data-proxy.schemaWritersSecretName" -}}
{{- if .Values.pgduckdb.existingSecret }}
{{- .Values.pgduckdb.existingSecret }}-schema-writers
{{- else }}
{{- include "data-proxy.fullname" . }}-schema-writers
{{- end }}
{{- end }}

{{- define "data-proxy.valkeySecretKey" -}}
{{- if .Values.valkey.auth.existingSecret }}
{{- .Values.valkey.auth.existingSecretKey }}
{{- else }}
{{- "password" }}
{{- end }}
{{- end }}

{{- define "data-proxy.pgduckdbMemberCount" -}}
1
{{- end }}

{{- define "data-proxy.pgduckdbStatefulSetName" -}}
{{- $root := .root -}}
{{- if $root.Values.ha.enabled -}}
{{- printf "%s-duckdb-%d" (include "data-proxy.fullname" $root) (.ordinal | int) -}}
{{- else -}}
{{- printf "%s-duckdb" (include "data-proxy.fullname" $root) -}}
{{- end -}}
{{- end }}

{{- define "data-proxy.schemaStackName" -}}
{{- printf "%s-%s" (include "data-proxy.fullname" .root) (.schema | replace "_" "-") -}}
{{- end }}

{{- define "data-proxy.schemaPvcName" -}}
{{- printf "pgdata-%s-%s-%d" (include "data-proxy.fullname" .root) (.schema | replace "_" "-") (.ordinal | int) -}}
{{- end }}

{{- define "data-proxy.schemaWriterDsn" -}}
{{- $root := .root -}}
{{- if $root.Values.ha.enabled -}}
postgresql://{{ $root.Values.pgduckdb.db.user }}:{{ $root.Values.pgduckdb.password }}@{{ include "data-proxy.schemaStackName" . }}-haproxy:5000/{{ $root.Values.pgduckdb.db.name }}
{{- else -}}
postgresql://{{ $root.Values.pgduckdb.db.user }}:{{ $root.Values.pgduckdb.password }}@{{ include "data-proxy.fullname" $root }}-duckdb:5432/{{ $root.Values.pgduckdb.db.name }}
{{- end -}}
{{- end }}

{{- define "data-proxy.schemaPatroniScope" -}}
{{- printf "%s-patroni" (include "data-proxy.schemaStackName" .) -}}
{{- end }}

{{- define "data-proxy.schemaMemberName" -}}
{{- printf "%s-%d" (include "data-proxy.schemaStackName" .) (.ordinal | int) -}}
{{- end }}

{{- define "data-proxy.pgduckdbPvcName" -}}
{{- $prefix := default (printf "pgdata-%s-duckdb" (include "data-proxy.fullname" .root)) .root.Values.pgduckdb.storage.claimNamePrefix -}}
{{- printf "%s-%d" $prefix (.ordinal | int) -}}
{{- end }}

{{- define "data-proxy.postgresDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.pgduckdb.db.name -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ include "data-proxy.fullname" . }}-duckdb:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.backupPgDsn" -}}
{{- $db := .Values.pgduckdb.db.name -}}
postgresql://backup:$(BACKUP_PASSWORD)@{{ include "data-proxy.fullname" . }}-duckdb:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.migrationDatabaseHost" -}}
{{- include "data-proxy.fullname" . }}-duckdb
{{- end }}

{{- define "data-proxy.appPgDsn" -}}
{{- $user := .Values.pgduckdb.db.user -}}
{{- $db   := .Values.pgduckdb.db.name -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ include "data-proxy.migrationDatabaseHost" . }}:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.nginxProxyConfig" -}}
{{ .Files.Get "files/nginx.conf" | replace "__PGRST_MAP__" (include "data-proxy.fallbackNginxUpstreams" .) | replace "__CACHE_TTL__" (toString .Values.fallback.cacheTtl) | replace "__MAX_BODY__" (toString .Values.fallback.maxCacheBodyBytes) | replace "__FETCH_BUFFER_SIZE__" (toString .Values.fallback.fetchBufferSize) | replace "__FETCH_TIMEOUT__" (toString .Values.fallback.fetchTimeout) }}
{{- end }}

{{- define "data-proxy.webdisConfig" -}}
{
  "redis_host": "{{ include "data-proxy.fullname" . }}-valkey",
  "redis_port": 6379,
  "redis_auth": "__VALKEY_PASSWORD__",
  "database": {{ .Values.fallback.cacheRedisDb }},
  "http_port": 7379,
  "daemonize": false,
  "logfile": "/dev/stdout"
}
{{- end }}

{{- define "data-proxy.jwtRules" -}}
jwtRules:
  - issuer: {{ .Values.ingress.auth.issuer | quote }}
    jwksUri: {{ .Values.ingress.auth.jwksUri | quote }}
    {{- with .Values.ingress.auth.audience }}
    audiences:
      - {{ . | quote }}
    {{- end }}
    forwardOriginalToken: true
{{- end }}

{{- define "data-proxy.fallbackNginxUpstreams" -}}
map $http_accept_profile $fallback_pgrst {
  default "http://{{ include "data-proxy.fullname" . }}-postgrest.{{ .Release.Namespace }}.svc.cluster.local:3000";
  {{- if and .Values.ha.enabled (not (empty .Values.ha.schemas)) }}
  {{- range $schema, $_ := .Values.ha.schemas }}
  {{ $schema | quote }} "http://{{ include "data-proxy.schemaStackName" (dict "root" $ "schema" $schema) }}-postgrest-ro.{{ $.Release.Namespace }}.svc.cluster.local:3000";
  {{- end }}
  {{- end }}
}
{{- end }}

{{- define "data-proxy.appEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.dbSecretName" . }}
      key: POSTGRES_PASSWORD
- name: PG_DSN
  value: {{ include "data-proxy.appPgDsn" . | quote }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.valkeySecretName" . }}
      key: {{ include "data-proxy.valkeySecretKey" . }}
- name: REDIS_URL
  value: "redis://:$(REDIS_PASSWORD)@{{ .Release.Name }}-valkey:6379/0"
- name: GCS_BUCKET
  value: {{ .Values.gcs.bucket | quote }}
- name: GCS_ENDPOINT
  value: {{ .Values.gcs.endpoint | quote }}
- name: GCS_USE_SSL
  value: {{ .Values.gcs.useSsl | quote }}
- name: GCS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.gcsSecretName" . }}
      key: GCS_KEY_ID
- name: GCS_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.gcsSecretName" . }}
      key: GCS_SECRET_KEY
- name: SYNC_CONFIG_PATH
  value: /config/sync.json
- name: FALLBACK_ENABLED
  value: {{ .Values.fallback.enabled | quote }}
- name: FALLBACK_CACHE_REDIS_DB
  value: {{ .Values.fallback.cacheRedisDb | quote }}
- name: DUMPER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.dumper.visibilityTimeoutMs | quote }}
- name: SEEDER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.seeder.visibilityTimeoutMs | quote }}
- name: PUBLISHER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.publisher.visibilityTimeoutMs | quote }}
- name: AUTH_ANON_ROLE
  value: {{ .Values.auth.anonRole | quote }}
- name: AUTH_USER_ROLE
  value: {{ .Values.auth.userRole | quote }}
- name: AUTH_AUTHENTICATOR_ROLE
  value: {{ .Values.auth.authenticatorRole | quote }}
- name: SCHEMA_WRITERS_FILE
  value: /config/schema-writers/writers.json
- name: PUSHGATEWAY_URL
  value: {{ .Values.pushgateway.url | default (printf "http://%s-pushgateway.%s.svc.cluster.local:9091" .Release.Name .Release.Namespace) | quote }}
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: /gcp/key.json
{{- end }}
{{- end }}

{{- define "data-proxy.gcpVolume" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp-key
  secret:
    secretName: {{ .Values.gcp.existingSecret }}
{{- end }}
{{- end }}

{{- define "data-proxy.gcpVolumeMount" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp-key
  mountPath: /gcp
  readOnly: true
{{- end }}
{{- end }}

{{- define "data-proxy.syncConfigVolume" -}}
- name: sync-config
  configMap:
    name: {{ include "data-proxy.fullname" . }}-sync
{{- end }}

{{- define "data-proxy.syncConfigVolumeMount" -}}
- name: sync-config
  mountPath: /config
  readOnly: true
{{- end }}

{{- define "data-proxy.schemaWritersVolume" -}}
- name: schema-writers
  secret:
    secretName: {{ include "data-proxy.schemaWritersSecretName" . }}
{{- end }}

{{- define "data-proxy.schemaWritersVolumeMount" -}}
- name: schema-writers
  mountPath: /config/schema-writers
  readOnly: true
{{- end }}
