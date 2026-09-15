{{/*
This file defines shared Helm template helpers for names, labels, secrets, and connection strings.
*/}}
{{- define "data-proxy.defaultCpuTrigger" -}}
- type: cpu
  metricType: Utilization
  metadata:
    value: "60"
{{- end }}

{{- define "data-proxy.defaultResourceTriggers" -}}
- type: cpu
  metricType: Utilization
  metadata:
    value: "60"
- type: memory
  metricType: Utilization
  metadata:
    value: "60"
{{- end }}

{{- define "data-proxy.haTriggers" -}}
{{- $root := .root -}}
{{- $schema := .schema -}}
{{- $component := .component -}}
{{- $override := dict -}}
{{- range $entry := $root.Values.ha.schemas }}
  {{- if eq $entry.name $schema }}
    {{- $override = $entry -}}
  {{- end }}
{{- end }}
{{- $triggers := dig $component "triggers" list $override }}
{{- if gt (len $triggers) 0 }}
{{ toYaml $triggers }}
{{- else if eq $component "postgres" }}
{{ include "data-proxy.defaultCpuTrigger" $root }}
{{- else }}
{{ include "data-proxy.defaultResourceTriggers" $root }}
{{- end }}
{{- end }}

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
{{- if .Values.postgres.existingSecret }}
{{- .Values.postgres.existingSecret }}
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

{{- define "data-proxy.s3SecretName" -}}
{{- if .Values.s3.existingSecret }}
{{- .Values.s3.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-s3
{{- end }}
{{- end }}

{{- define "data-proxy.s3Endpoint" -}}
{{- if .Values.s3.endpoint }}
{{- .Values.s3.endpoint }}
{{- else if .Values.seaweedfs.enabled }}
{{- if .Values.seaweedfs.allInOne.enabled }}
{{- printf "%s-seaweedfs-all-in-one:8333" .Release.Name }}
{{- else }}
{{- printf "%s-seaweedfs-s3:8333" .Release.Name }}
{{- end }}
{{- else }}
{{- fail "s3.endpoint is required when the SeaweedFS subchart is disabled" }}
{{- end }}
{{- end }}

{{- define "data-proxy.redisSecretName" -}}
{{- required "redis.existingSecret is required" .Values.redis.existingSecret }}
{{- end }}

{{- define "data-proxy.redisConfigKey" -}}
{{- .Values.redis.configKey | default "REDIS" }}
{{- end }}

{{- define "data-proxy.redisPasswordKey" -}}
{{- .Values.redis.passwordKey | default "REDIS_PASSWORD" }}
{{- end }}

{{- define "data-proxy.redisWriterAddress" -}}
{{- required "redis.writerAddress is required for KEDA Redis Streams triggers" .Values.redis.writerAddress }}
{{- end }}

{{- define "data-proxy.schemaWritersSecretName" -}}
{{- if .Values.postgres.existingSecret }}
{{- .Values.postgres.existingSecret }}-schema-writers
{{- else }}
{{- include "data-proxy.fullname" . }}-schema-writers
{{- end }}
{{- end }}

{{- define "data-proxy.redisConfigEnv" -}}
- name: REDIS
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.redisSecretName" . }}
      key: {{ include "data-proxy.redisConfigKey" . }}
{{- end }}

{{- define "data-proxy.cnpgClusterName" -}}
{{- $root := .root -}}
{{- if eq $root.Values.cnpg.mode "shared" -}}
{{- include "data-proxy.fullname" $root -}}
{{- else -}}
{{- printf "%s-%s" (include "data-proxy.fullname" $root) (.schema | replace "_" "-") -}}
{{- end -}}
{{- end }}

{{- define "data-proxy.schemaWriterDsn" -}}
{{- $root := .root -}}
{{- $cluster := include "data-proxy.cnpgClusterName" . -}}
postgresql://{{ $root.Values.postgres.db.user }}:{{ $root.Values.postgres.password }}@{{ $cluster }}-rw:5432/{{ $root.Values.postgres.db.name }}
{{- end }}

{{- define "data-proxy.postgresDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.postgres.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ $cluster }}-pooler-ro:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.backupPgDsn" -}}
{{- $db := .Values.postgres.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgresql://backup:$(BACKUP_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.migrationDatabaseHost" -}}
{{- include "data-proxy.fullname" . }}-rw
{{- end }}

{{- define "data-proxy.appPgDsn" -}}
{{- $user := .Values.postgres.db.user -}}
{{- $db   := .Values.postgres.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.nginxProxyConfig" -}}
{{ .Files.Get "files/nginx.conf" | replace "__PGRST_MAP__" (include "data-proxy.fallbackNginxUpstreams" .) | replace "__CACHE_TTL__" (toString .Values.fallback.cacheTtl) | replace "__MAX_BODY__" (toString .Values.fallback.maxCacheBodyBytes) | replace "__FETCH_BUFFER_SIZE__" (toString .Values.fallback.fetchBufferSize) | replace "__FETCH_TIMEOUT__" (toString .Values.fallback.fetchTimeout) | replace "__FETCH_KEEPALIVE__" (toString .Values.fallback.fetchKeepalive) | replace "__FETCH_KEEPALIVE_TIMEOUT__" (toString .Values.fallback.fetchKeepaliveTimeout) }}
{{- end }}

{{- define "data-proxy.webdisWriteConfig" -}}
{
  "redis_host": "{{ .Values.redis.webdisHost }}",
  "redis_port": {{ .Values.redis.webdisPort }},
  "redis_auth": "__VALKEY_PASSWORD__",
  "database": {{ .Values.fallback.cacheRedisDb }},
  "http_port": 7379,
  "daemonize": false,
  "logfile": "/dev/stdout"
}
{{- end }}

{{- define "data-proxy.webdisReadConfig" -}}
{
  "redis_host": "{{ .Values.redis.webdisReadHost | default .Values.redis.webdisHost }}",
  "redis_port": {{ .Values.redis.webdisReadPort | default .Values.redis.webdisPort }},
  "redis_auth": "__VALKEY_PASSWORD__",
  "database": {{ .Values.fallback.cacheRedisDb }},
  "http_port": 7380,
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
  {{- if eq .Values.cnpg.mode "per-schema" }}
  {{- $schemas := .Values.cnpg.schemas }}
  {{- if .Values.ha.enabled }}{{ $schemas = .Values.syncConfig.schemas }}{{ end }}
  {{- range $schema, $_ := $schemas }}
  {{ $schema | quote }} "http://{{ include "data-proxy.cnpgClusterName" (dict "root" $ "schema" $schema) }}-postgrest-ro.{{ $.Release.Namespace }}.svc.cluster.local:3000";
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
{{ include "data-proxy.redisConfigEnv" . }}
- name: S3_BUCKET
  value: {{ .Values.s3.bucket | quote }}
- name: S3_ENDPOINT
  value: {{ include "data-proxy.s3Endpoint" . | quote }}
- name: S3_USE_SSL
  value: {{ .Values.s3.useSsl | quote }}
- name: S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.s3SecretName" . }}
      key: S3_ACCESS_KEY
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.s3SecretName" . }}
      key: S3_SECRET_KEY
- name: SYNC_CONFIG_PATH
  value: /config/sync.json
- name: FALLBACK_CACHE_REDIS_DB
  value: {{ .Values.fallback.cacheRedisDb | quote }}
- name: DUMPER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.dumper.visibilityTimeoutMs | int64 | quote }}
- name: DUMPER_BATCH_BYTES
  value: {{ .Values.dumper.batchMegaBytes | mul 1048576 | int64 | quote }}
- name: DUMPER_BATCH_MAX_PARTITIONS
  value: {{ .Values.dumper.batchMaxPartitions | quote }}
- name: DUMPER_SCRATCH_DIR
  value: {{ .Values.dumper.scratch.mountPath | quote }}
- name: SEEDER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.seeder.visibilityTimeoutMs | int64 | quote }}
- name: PUBLISHER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.publisher.visibilityTimeoutMs | int64 | quote }}
- name: AUTH_ANON_ROLE
  value: {{ .Values.auth.anonRole | quote }}
- name: AUTH_USER_ROLE
  value: {{ .Values.auth.userRole | quote }}
- name: AUTH_AUTHENTICATOR_ROLE
  value: {{ .Values.auth.authenticatorRole | quote }}
- name: SCHEMA_WRITERS
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.schemaWritersSecretName" . }}
      key: writers.json
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
