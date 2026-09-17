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
{{- if .Values.cnpg.existingSecret }}
{{- .Values.cnpg.existingSecret }}
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
{{- if .Values.cnpg.existingSecret }}
{{- .Values.cnpg.existingSecret }}-schema-writers
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
postgresql://{{ $root.Values.cnpg.db.user }}:{{ $root.Values.cnpg.password }}@{{ $cluster }}-rw:5432/{{ $root.Values.cnpg.db.name }}
{{- end }}

{{- define "data-proxy.postgresReadDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ $cluster }}-pooler-ro:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.postgresWriteDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.postgresDsn" -}}
{{ include "data-proxy.postgresWriteDsn" . }}
{{- end }}

{{- define "data-proxy.backupPgDsn" -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgresql://backup:$(BACKUP_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.migrationDatabaseHost" -}}
{{- include "data-proxy.fullname" . }}-rw
{{- end }}

{{- define "data-proxy.appPgDsn" -}}
{{- $user := .Values.cnpg.db.user -}}
{{- $db   := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.nginxConfigBody" -}}
{{- $upstreams := .upstreams -}}
{{- $root := .root -}}
{{ $root.Files.Get "files/nginx.conf" | replace "__PGRST_MAP__" $upstreams | replace "__CACHE_TTL__" (toString $root.Values.fallback.cacheTtl) | replace "__MAX_BODY__" (toString $root.Values.fallback.maxCacheBodyBytes) | replace "__FETCH_BUFFER_SIZE__" (toString $root.Values.fallback.fetchBufferSize) | replace "__FETCH_TIMEOUT__" (toString $root.Values.fallback.fetchTimeout) | replace "__FETCH_KEEPALIVE__" (toString $root.Values.fallback.fetchKeepalive) | replace "__FETCH_KEEPALIVE_TIMEOUT__" (toString $root.Values.fallback.fetchKeepaliveTimeout) }}
{{- end }}

{{- define "data-proxy.nginxProxyConfig" -}}
{{ include "data-proxy.nginxConfigBody" (dict "root" . "upstreams" (include "data-proxy.fallbackNginxUpstreams" .)) }}
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
map $http_accept_profile $postgrest_read {
  default "http://{{ include "data-proxy.fullname" . }}-postgrest-ro.{{ .Release.Namespace }}.svc.cluster.local:3000";
  {{- if eq .Values.cnpg.mode "per-schema" }}
  {{- range $schema, $_ := (default .Values.cnpg.schemas .Values.syncConfig.schemas) }}
  {{ $schema | quote }} "http://{{ include "data-proxy.cnpgClusterName" (dict "root" $ "schema" $schema) }}-postgrest-ro.{{ $.Release.Namespace }}.svc.cluster.local:3000";
  {{- end }}
  {{- end }}
}
map $http_accept_profile $postgrest_write {
  default "http://{{ include "data-proxy.fullname" . }}-postgrest-rw.{{ .Release.Namespace }}.svc.cluster.local:3000";
  {{- if eq .Values.cnpg.mode "per-schema" }}
  {{- range $schema, $_ := (default .Values.cnpg.schemas .Values.syncConfig.schemas) }}
  {{ $schema | quote }} "http://{{ include "data-proxy.cnpgClusterName" (dict "root" $ "schema" $schema) }}-postgrest-rw.{{ $.Release.Namespace }}.svc.cluster.local:3000";
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
  value: {{ .Values.sync.dumper.visibilityTimeoutMs | int64 | quote }}
- name: DUMPER_BATCH_BYTES
  value: {{ .Values.sync.dumper.batchMegaBytes | mul 1048576 | int64 | quote }}
- name: DUMPER_BATCH_MAX_PARTITIONS
  value: {{ .Values.sync.dumper.batchMaxPartitions | quote }}
- name: DUMPER_SCRATCH_DIR
  value: {{ .Values.sync.dumper.scratch.mountPath | quote }}
- name: SEEDER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.sync.seeder.visibilityTimeoutMs | int64 | quote }}
- name: PUBLISHER_VISIBILITY_TIMEOUT_MS
  value: {{ .Values.sync.publisher.visibilityTimeoutMs | int64 | quote }}
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
- name: KUBERNETES_NAMESPACE
  value: {{ .Release.Namespace | quote }}
- name: POSTGREST_RO_DEPLOYMENT_TEMPLATE
  value: {{ if eq .Values.cnpg.mode "shared" }}{{ printf "%s-postgrest-ro" (include "data-proxy.fullname" .) | quote }}{{ else }}{{ printf "%s-{}-postgrest-ro" (include "data-proxy.fullname" .) | quote }}{{ end }}
- name: POSTGREST_RW_DEPLOYMENT_TEMPLATE
  value: {{ if eq .Values.cnpg.mode "shared" }}{{ printf "%s-postgrest-rw" (include "data-proxy.fullname" .) | quote }}{{ else }}{{ printf "%s-{}-postgrest-rw" (include "data-proxy.fullname" .) | quote }}{{ end }}
- name: POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS
  value: "300"
- name: REPLICATION_WAIT_TIMEOUT_SECONDS
  value: "300"
- name: REPLICATION_POLL_INTERVAL_SECONDS
  value: "1"
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
