
{{/*
{
  "kind": "macro",
  "name": "data-proxy.defaultCpuTrigger",
  "description": "Render the defaultCpuTrigger Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.defaultCpuTrigger" -}}
- type: cpu
  metricType: Utilization
  metadata:
    value: "90"
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.defaultResourceTriggers",
  "description": "Render the defaultResourceTriggers Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.defaultResourceTriggers" -}}
- type: cpu
  metricType: Utilization
  metadata:
    value: "90"
- type: memory
  metricType: Utilization
  metadata:
    value: "90"
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.haTriggers",
  "description": "Render the haTriggers Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.haTriggers" -}}
{{- $root := .root -}}
{{- $schema := .schema -}}
{{- $component := .component -}}
{{- $override := dict -}}
{{- range $entry := (list) }}
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

{{/*
{
  "kind": "macro",
  "name": "data-proxy.name",
  "description": "Render the name Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.fullname",
  "description": "Render the fullname Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.labels",
  "description": "Render the labels Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "data-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.selectorLabels",
  "description": "Render the selectorLabels Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "data-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.serviceAccountName",
  "description": "Render the serviceAccountName Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- .Values.serviceAccount.name | default (include "data-proxy.fullname" .) }}
{{- else }}
{{- .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.dbSecretName",
  "description": "Render the dbSecretName Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.dbSecretName" -}}
{{- if .Values.cnpg.existingSecret }}
{{- .Values.cnpg.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-cnpg-data-proxy
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.authenticatorSecretName",
  "description": "Render the authenticatorSecretName Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.authenticatorSecretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- printf "%s-cnpg-authenticator" (include "data-proxy.fullname" .) }}
{{- end }}
{{- end }}

{{- define "data-proxy.jobsSecretName" -}}
{{- if .Values.jobs.existingSecret }}
{{- .Values.jobs.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-jobs
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.s3SecretName",
  "description": "Render the s3SecretName Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.s3SecretName" -}}
{{- if .Values.s3.existingSecret }}
{{- .Values.s3.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-s3
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.s3Endpoint",
  "description": "Render the s3Endpoint Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
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

{{/*
{
  "kind": "macro",
  "name": "data-proxy.redisSecretName",
  "description": "Render the redisSecretName Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.redisSecretName" -}}
{{- required "redis.existingSecret is required" .Values.redis.existingSecret }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.redisPasswordKey",
  "description": "Render the redisPasswordKey Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.redisPasswordKey" -}}
{{- .Values.redis.passwordKey | default "REDIS_PASSWORD" }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.redisWriterAddress",
  "description": "Render the redisWriterAddress Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.redisWriterAddress" -}}
{{- $address := required "redis.writerAddress is required for KEDA Redis Streams triggers" .Values.redis.writerAddress }}
{{- if contains ".svc." $address }}
{{- $address }}
{{- else }}
{{- $parts := splitList ":" $address }}
{{- printf "%s.%s.svc.cluster.local:%s" (index $parts 0) .Release.Namespace (index $parts 1) }}
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.redisConfigEnv",
  "description": "Render the redisConfigEnv Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.redisConfigEnv" -}}
- name: REDIS_READ
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.redisSecretName" . }}
      key: {{ .Values.redis.readKey | default "REDIS_READ" }}
- name: REDIS_WRITE
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.redisSecretName" . }}
      key: {{ .Values.redis.writeKey | default "REDIS_WRITE" }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.cnpgClusterName",
  "description": "Render the cnpgClusterName Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.cnpgClusterName" -}}
{{- $root := .root -}}
{{- include "data-proxy.fullname" $root -}}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.postgresReadDsn",
  "description": "Render the postgresReadDsn Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.postgresReadDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ $cluster }}-pooler-ro:5432/{{ $db }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.postgresWriteDsn",
  "description": "Render the postgresWriteDsn Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.postgresWriteDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.postgresDsn",
  "description": "Render the postgresDsn Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.postgresDsn" -}}
{{ include "data-proxy.postgresWriteDsn" . }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.appPgDsn",
  "description": "Render the appPgDsn Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.appPgDsn" -}}
{{- $user := .Values.cnpg.db.user -}}
{{- $db   := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{/*
{
  "kind": "helper",
  "name": "data-proxy.dbosClusterName",
  "description": "Render the DBOS CNPG cluster name for the single cluster.",
  "inputs": {
    "context": "Helm template context."
  }
}
*/}}
{{- define "data-proxy.dbosClusterName" -}}
{{- include "data-proxy.fullname" . -}}
{{- end }}

{{/*
{
  "kind": "helper",
  "name": "data-proxy.dbosSystemDatabaseUrl",
  "description": "Render the DBOS system database URL for the DBOS cluster.",
  "inputs": {
    "context": "Helm template context."
  }
}
*/}}
{{- define "data-proxy.dbosSystemDatabaseUrl" -}}
{{- $user := .Values.cnpg.db.user -}}
{{- $db   := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.dbosClusterName" . -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.nginxConfigBody",
  "description": "Render the nginxConfigBody Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.nginxConfigBody" -}}
{{- $upstreams := .upstreams -}}
{{- $root := .root -}}
{{ $root.Files.Get "files/nginx.conf" | replace "__PGRST_MAP__" $upstreams }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.nginxProxyConfig",
  "description": "Render the nginxProxyConfig Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.nginxProxyConfig" -}}
{{ include "data-proxy.nginxConfigBody" (dict "root" . "upstreams" (include "data-proxy.fallbackNginxUpstreams" .)) }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.webdisWriteConfig",
  "description": "Render the webdisWriteConfig Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
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

{{/*
{
  "kind": "macro",
  "name": "data-proxy.webdisReadConfig",
  "description": "Render the webdisReadConfig Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
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

{{/*
{
  "kind": "macro",
  "name": "data-proxy.jwtRules",
  "description": "Render the jwtRules Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
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

{{/*
{
  "kind": "macro",
  "name": "data-proxy.fallbackNginxUpstreams",
  "description": "Render the fallbackNginxUpstreams Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.fallbackNginxUpstreams" -}}
map $http_accept_profile $postgrest_read {
  default "http://{{ include "data-proxy.fullname" . }}-postgrest.{{ .Release.Namespace }}.svc.cluster.local:3000";
}

{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.litestreamConfig",
  "description": "Render Litestream replication entries for every schema catalog.",
  "inputs": {
    "values": "Helm chart values used by this resource.",
    "release": "Helm release context used in resource names."
  },
  "returns": "Litestream YAML configuration."
}
*/}}
{{- define "data-proxy.litestreamConfig" -}}
{{- $root := .root | default . -}}
{{- $schemas := .schemas | default ($root.Values.sync.config.schemas) -}}
{{- $localPath := .localPath | default $root.Values.ducklake.catalogLocalPath -}}
{{- if eq (len $schemas) 0 }}
dbs: []
{{- else }}
dbs:
{{- range $schema, $_ := $schemas }}
  - path: {{ printf "%s/%s/catalog.sqlite" $localPath $schema | quote }}
    replica:
      type: s3
      bucket: {{ $root.Values.s3.bucket | quote }}
      path: {{ printf "%s/%s/catalog.sqlite" $root.Values.ducklake.catalogPath $schema | quote }}
      endpoint: {{ printf "%s://%s" (ternary "https" "http" (eq $root.Values.s3.useSsl "true")) (include "data-proxy.s3Endpoint" $root) | quote }}
      access-key-id: ${S3_ACCESS_KEY}
      secret-access-key: ${S3_SECRET_KEY}
{{- end }}
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.litestreamRestoreScript",
  "description": "Render the restore supervisor for all schema catalogs.",
  "inputs": {
    "values": "Helm chart values used by this resource."
  },
  "returns": "POSIX shell script."
}
*/}}
{{- define "data-proxy.litestreamRestoreScript" -}}
#!/bin/sh
set -eu
{{- $schemas := .Values.sync.config.schemas }}
{{- range $schema, $_ := $schemas }}
(
  db={{ printf "%s/%s/catalog.sqlite" $.Values.ducklake.catalogLocalPath $schema }}
  dir={{ printf "%s/%s" $.Values.ducklake.catalogLocalPath $schema }}
  while true; do
    tmp="${db}.restore"
    rm -f "$tmp"
    if litestream restore -if-replica-exists -config /projected/litestream-read.yaml -o "$tmp" "$db"; then
      if [ -f "$tmp" ]; then
        mkdir -p "$dir"
        mv "$tmp" "$db"
      fi
    fi
    if [ -f "$db" ]; then
      exec litestream restore -f -config /projected/litestream-read.yaml "$db"
    fi
    sleep 5
  done
) &
{{- end }}
wait
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.cnpgCatalogPodPatch",
  "description": "Patch CNPG instance pods with the shared read-only catalog PVC.",
  "inputs": {
    "values": "Helm chart values used by this resource."
  },
  "returns": "Kubernetes JSON patch."
}
*/}}
{{- define "data-proxy.cnpgCatalogPodPatch" -}}
{{- $volume := dict
  "name" "ducklake-catalogs"
  "persistentVolumeClaim" (dict
    "claimName" (printf "%s-catalog-reader" (include "data-proxy.fullname" .))
    "readOnly" true
  )
-}}
{{- $mount := dict
  "name" "ducklake-catalogs"
  "mountPath" .Values.ducklake.catalogLocalPath
  "readOnly" true
-}}
{{- list
  (dict "op" "add" "path" "/spec/volumes/-" "value" $volume)
  (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" $mount)
  | toJson
-}}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.appEnv",
  "description": "Render the appEnv Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.appEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.dbSecretName" . }}
      key: POSTGRES_PASSWORD
- name: PG_DATABASE_URL
  value: {{ include "data-proxy.appPgDsn" . | quote }}
{{ include "data-proxy.redisConfigEnv" . }}
- name: S3_BUCKET
  value: {{ .Values.s3.bucket | quote }}
- name: S3_ENDPOINT
  value: {{ include "data-proxy.s3Endpoint" . | quote }}
- name: S3_USE_SSL
  value: {{ .Values.s3.useSsl | quote }}
- name: S3_SCRATCH_PREFIX
  value: tmp
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
- name: DUCKLAKE_CATALOG_LOCAL_PATH
  value: {{ .Values.ducklake.catalogLocalPath | quote }}
- name: DUCKLAKE_CATALOG_PATH
  value: {{ .Values.ducklake.catalogPath | quote }}
- name: DUCKLAKE_TARGET_FILE_SIZE
  value: {{ .Values.ducklake.targetFileSize | quote }}
- name: DUCKLAKE_SNAPSHOT_EXPIRATION
  value: {{ .Values.ducklake.snapshotExpiration | quote }}
- name: EMPTY_CACHE_TTL
  value: {{ .Values.fallback.emptyCacheTtl | quote }}
- name: SYNC_CONFIG_PATH
  value: /config/sync.json
- name: FALLBACK_CACHE_REDIS_DB
  value: {{ .Values.fallback.cacheRedisDb | quote }}
- name: DUMPER_BATCH_BYTES
  value: "0"
- name: DUMPER_BATCH_MAX_PARTITIONS
  value: {{ .Values.sync.dumpTaskFileLimit | quote }}
- name: DUMPER_SCRATCH_DIR
  value: /tmp
- name: DUMP_QUEUE_MAX_ATTEMPTS
  value: {{ .Values.sync.dumpStepMaxAttempts | quote }}
- name: DUMP_QUEUE_RATE_LIMIT
  value: {{ .Values.sync.dumpQueueRateLimit | quote }}
- name: SYNC_STEP_MAX_ATTEMPTS
  value: {{ .Values.sync.stepMaxAttempts | quote }}
- name: SYNC_RUN_TIMEOUT_SECONDS
  value: {{ .Values.sync.workflowTimeoutSeconds | quote }}
- name: DBOS_SYSTEM_DATABASE_URL
  value: {{ include "data-proxy.dbosSystemDatabaseUrl" . | quote }}
- name: AIRFLOW_CONN_AIRFLOW_DB
  value: {{ include "data-proxy.dbosSystemDatabaseUrl" . | quote }}
- name: DBOS_APPLICATION_NAME
  value: {{ .Values.sync.dbos.applicationName | quote }}
- name: DBOS_APPLICATION_VERSION
  value: {{ .Values.sync.dbos.applicationVersion | quote }}
- name: DBOS_SYSTEM_SCHEMA
  value: {{ .Values.sync.dbos.systemSchema | quote }}
- name: DBOS_APP_SCHEMA
  value: {{ .Values.sync.dbos.appSchema | quote }}
- name: OTLP_LOGS_ENDPOINT
  value: {{ .Values.observability.otlpLogsEndpoint | quote }}
- name: OTLP_TRACES_ENDPOINT
  value: {{ .Values.observability.otlpTracesEndpoint | quote }}
- name: OTLP_METRICS_ENDPOINT
  value: {{ .Values.observability.otlpMetricsEndpoint | quote }}
- name: SYNC_SCHEDULE
  value: {{ .Values.sync.schedule | quote }}
- name: DUMP_QUEUE_WORKER_CONCURRENCY
  value: {{ .Values.sync.dumpQueueWorkerConcurrency | quote }}
- name: SYNC_QUEUE_CONCURRENCY
  value: {{ .Values.sync.queueConcurrency | quote }}
- name: AUTH_ANON_ROLE
  value: {{ .Values.auth.anonRole | quote }}
- name: AUTH_USER_ROLE
  value: {{ .Values.auth.userRole | quote }}
- name: AUTH_AUTHENTICATOR_ROLE
  value: {{ .Values.auth.authenticatorRole | quote }}
- name: KUBERNETES_NAMESPACE
  value: {{ .Release.Namespace | quote }}
- name: POSTGREST_DEPLOYMENT_TEMPLATE
  value: {{ printf "%s-postgrest" (include "data-proxy.fullname" .) | quote }}
- name: POSTGREST_ROLLOUT_TIMEOUT_SECONDS
  value: "300"
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: {{ .Values.gcp.mountPath | quote }}
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.gcpVolume",
  "description": "Render the gcpVolume Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.gcpVolume" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp-key
  secret:
    secretName: {{ .Values.gcp.existingSecret }}
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.gcpVolumeMount",
  "description": "Render the gcpVolumeMount Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.gcpVolumeMount" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp-key
  mountPath: {{ dir .Values.gcp.mountPath }}
  readOnly: true
{{- end }}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.syncConfigVolume",
  "description": "Render the syncConfigVolume Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.syncConfigVolume" -}}
- name: sync-config
  configMap:
    name: {{ include "data-proxy.fullname" . }}-sync
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.syncConfigVolumeMount",
  "description": "Render the syncConfigVolumeMount Helm helper.",
  "inputs": {
    "context": "Helm template context."
  },
  "returns": "Helper-rendered Kubernetes or configuration content."
}
*/}}
{{- define "data-proxy.syncConfigVolumeMount" -}}
- name: sync-config
  mountPath: /config
  readOnly: true
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.duckdbThreads",
  "description": "Compute duckdb.threads proportional to the CNPG memory limit: floor(GiB / 2), clamped to 1..4.",
  "inputs": {
    "context": "Helm template context with cnpg.resources.limits.memory."
  },
  "returns": "Thread count as a quoted string for the postgresql.parameters map."
}
*/}}
{{- define "data-proxy.duckdbThreads" -}}
{{- $mem := .Values.cnpg.resources.limits.memory | toString -}}
{{- $bytes := 0 -}}
{{- if (regexMatch "^[0-9]+(\\.[0-9]+)?(Gi|Mi|Ti|Ki)?$" $mem) -}}
  {{- $num := regexFind "^[0-9]+(\\.[0-9]+)?" $mem | float64 -}}
  {{- $unit := regexFind "(Gi|Mi|Ti|Ki)$" $mem -}}
  {{- $mult := dict "Ki" 1024.0 "Mi" 1048576.0 "Gi" 1073741824.0 "Ti" 1099511627776.0 -}}
  {{- $factor := 1.0 -}}
  {{- if $unit -}}
    {{- $factor = index $mult $unit -}}
  {{- end -}}
  {{- $bytes = mulf $num $factor -}}
{{- end -}}
{{- $gib := divf $bytes 1073741824.0 -}}
{{- $threads := floor (divf $gib 2.0) | int -}}
{{- if lt $threads 1 -}}
  {{- $threads = 1 -}}
{{- end -}}
{{- if gt $threads 4 -}}
  {{- $threads = 4 -}}
{{- end -}}
{{- $threads | quote -}}
{{- end }}

{{/*
{
  "kind": "macro",
  "name": "data-proxy.cnpgParameters",
  "description": "Render tunable PostgreSQL parameters for CNPG, merging chart defaults with user overrides from .Values.cnpg.postgresql.parameters.",
  "inputs": {
    "context": "Helm template context with cnpg.postgresql.parameters."
  },
  "returns": "YAML parameters block for tunable postgresql.parameters."
}
*/}}
{{- define "data-proxy.cnpgParameters" -}}
{{- $defaults := dict
  "shared_buffers" "1GB"
  "work_mem" "16MB"
  "maintenance_work_mem" "512MB"
  "effective_cache_size" "5GB"
  "max_parallel_workers_per_gather" "4"
  "random_page_cost" "1.1"
  "effective_io_concurrency" "200"
  "max_connections" "200"
  "max_wal_size" "4GB"
  "min_wal_size" "1GB"
  "wal_compression" "on"
  "checkpoint_timeout" "15min"
  "checkpoint_completion_target" "0.9"
-}}
{{- $params := mergeOverwrite $defaults (default (dict) .Values.cnpg.postgresql.parameters) -}}
{{- toYaml $params -}}
{{- end }}
