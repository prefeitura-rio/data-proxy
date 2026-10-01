
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

{{- define "data-proxy.postgrestDeployments" -}}
{{- $name := printf "%s-postgrest" (include "data-proxy.fullname" .) -}}
{{- $names := list $name -}}
{{- if .Values.ha.enabled -}}
{{- $names = append $names (printf "%s-ro" $name) -}}
{{- end -}}
{{- $names | toJson -}}
{{- end }}

{{- define "data-proxy.cnpgMinInstances" -}}
{{- max (.Values.cnpg.autoscaling.minReplicaCount | int) 3 -}}
{{- end }}

{{- define "data-proxy.cnpgMaxInstances" -}}
{{- max (.Values.cnpg.autoscaling.maxReplicaCount | int) (include "data-proxy.cnpgMinInstances" . | int) -}}
{{- end }}

{{- define "data-proxy.cnpgCpuLimitCores" -}}
{{- $cpu := .Values.cnpg.resources.limits.cpu | toString -}}
{{- if hasSuffix "m" $cpu -}}
{{- divf (trimSuffix "m" $cpu | float64) 1000.0 | ceil | int -}}
{{- else -}}
{{- $cpu | float64 | ceil | int -}}
{{- end -}}
{{- end }}

{{- define "data-proxy.cnpgSessionsPerInstance" -}}
{{- $threads := include "data-proxy.duckdbThreads" . | trimAll "\"" | float64 -}}
{{- max 1 (divf (include "data-proxy.cnpgCpuLimitCores" . | float64) $threads | ceil | int) -}}
{{- end }}

{{- define "data-proxy.cnpgDefaultTriggers" -}}
- type: postgresql
  metricType: Value
  authenticationRef:
    name: {{ include "data-proxy.fullname" . }}-cluster-autoscaler-auth
  metadata:
    host: {{ printf "%s-r.%s.svc.cluster.local" (include "data-proxy.fullname" .) .Release.Namespace }}
    port: "5432"
    userName: {{ .Values.cnpg.db.user }}
    dbName: {{ .Values.cnpg.db.name }}
    sslmode: require
    query: {{ printf "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND backend_type = 'client backend' AND usename = '%s'" .Values.auth.authenticatorRole | quote }}
    targetQueryValue: {{ include "data-proxy.cnpgSessionsPerInstance" . | quote }}
{{- end }}

{{- define "data-proxy.poolerDefaultTriggers" -}}
{{- $root := .root -}}
- type: kubernetes-workload
  metadata:
    podSelector: {{ printf "app.kubernetes.io/name=%s,app.kubernetes.io/instance=%s,app.kubernetes.io/component=%s" (include "data-proxy.name" $root) $root.Release.Name .component | quote }}
    value: {{ max 1 (div ($root.Values.cnpg.pooler.poolSize | int) 10) | quote }}
{{- end }}

{{- define "data-proxy.triggers" -}}
{{- if gt (len (.triggers | default list)) 0 -}}
{{ toYaml .triggers }}
{{- else -}}
{{ .default }}
{{- end -}}
{{- end }}

{{- /*
One ScaledObject per workload whose count a scaler owns. A fixed workload sets "pinned": KEDA holds it
at that count, so Helm never sets a count that KEDA also writes and a mode switch is one server-side apply.
*/ -}}
{{- define "data-proxy.scaledObject" -}}
{{- $root := .root -}}
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {{ .name }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    {{- include "data-proxy.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .component }}
  {{- if hasKey . "pinned" }}
  annotations:
    autoscaling.keda.sh/paused-replicas: {{ .pinned | quote }}
  {{- end }}
spec:
  scaleTargetRef:
    {{- toYaml .target | nindent 4 }}
  minReplicaCount: {{ ternary .pinned .min (hasKey . "pinned") }}
  maxReplicaCount: {{ ternary .pinned .max (hasKey . "pinned") }}
  pollingInterval: {{ .block.pollingInterval }}
  cooldownPeriod: {{ .block.cooldownPeriod }}
  triggers:
    {{- .triggers | nindent 4 }}
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
{{- include "data-proxy.fullname" . }}-cnpg-superuser
{{- end }}

{{- define "data-proxy.authenticatorSecretName" -}}
{{- printf "%s-cnpg-authenticator" (include "data-proxy.fullname" .) }}
{{- end }}

{{- define "data-proxy.s3SecretName" -}}
{{- include "data-proxy.fullname" . }}-s3
{{- end }}

{{- define "data-proxy.s3CredentialsChecksum" -}}
{{- $s3Name := include "data-proxy.s3SecretName" . -}}
{{- $s3 := lookup "v1" "Secret" .Release.Namespace $s3Name -}}
{{- $accessKey := "data-proxy" -}}
{{- $secretKey := "" -}}
{{- if $s3 -}}
{{- if hasKey $s3.data "S3_ACCESS_KEY" -}}
{{- $accessKey = index $s3.data "S3_ACCESS_KEY" | b64dec -}}
{{- end -}}
{{- if hasKey $s3.data "S3_SECRET_KEY" -}}
{{- $secretKey = index $s3.data "S3_SECRET_KEY" | b64dec -}}
{{- end -}}
{{- end -}}
{{- if .Values.s3.accessKey -}}
{{- $accessKey = .Values.s3.accessKey -}}
{{- end -}}
{{- if .Values.s3.secretKey -}}
{{- $secretKey = .Values.s3.secretKey -}}
{{- end -}}
{{- printf "%s:%s" $accessKey $secretKey | sha256sum -}}
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
{{- .Values.redis.existingSecret | default (printf "%s-redis" (include "data-proxy.fullname" .)) }}
{{- end }}

{{- define "data-proxy.redisPasswordKey" -}}
{{- .Values.redis.passwordKey | default "REDIS_PASSWORD" }}
{{- end }}

{{- define "data-proxy.redisWriterAddress" -}}
{{- $address := required "redis.writerAddress is required for KEDA Redis Streams triggers" .Values.redis.writerAddress }}
{{- if contains ".svc." $address }}
{{- $address }}
{{- else }}
{{- $parts := splitList ":" $address }}
{{- printf "%s.%s.svc.cluster.local:%s" (index $parts 0) .Release.Namespace (index $parts 1) }}
{{- end }}
{{- end }}

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

{{- define "data-proxy.cnpgClusterName" -}}
{{- $root := .root -}}
{{- include "data-proxy.fullname" $root -}}
{{- end }}

{{- define "data-proxy.postgresPoolerDsn" -}}
{{- $root := .root -}}
{{- $role := $root.Values.auth.authenticatorRole -}}
{{- $db := $root.Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" $root -}}
postgres://{{ $role }}:$(PGRST_PASSWORD)@{{ $cluster }}-{{ .pooler }}:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.postgresWriteDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.postgresDsn" -}}
{{ include "data-proxy.postgresWriteDsn" . }}
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
postgresql://{{ $user }}:$(PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
{{- end }}

{{- define "data-proxy.nginxConfigBody" -}}
{{- $upstreams := .upstreams -}}
{{- $root := .root -}}
{{ $root.Files.Get "files/nginx.conf" | replace "__PGRST_MAP__" $upstreams }}
{{- end }}

{{- define "data-proxy.nginxProxyConfig" -}}
{{ include "data-proxy.nginxConfigBody" (dict "root" . "upstreams" (include "data-proxy.nginxUpstreams" .)) }}
{{- end }}

{{- define "data-proxy.webdisWriteConfig" -}}
{
  "redis_host": "{{ .Values.redis.webdisHost }}",
  "redis_port": {{ .Values.redis.webdisPort }},
  "redis_auth": "__VALKEY_PASSWORD__",
  "database": {{ .Values.proxy.cacheRedisDb }},
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
  "database": {{ .Values.proxy.cacheRedisDb }},
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

{{- define "data-proxy.nginxUpstreams" -}}
{{- $postgrest := printf "%s-postgrest" (include "data-proxy.fullname" .) -}}
map $http_accept_profile $postgrest_read {
  default "http://{{ $postgrest }}{{ ternary "-ro" "" .Values.ha.enabled }}.{{ .Release.Namespace }}.svc.cluster.local:3000";
}
{{- if .Values.ha.enabled }}

map $http_accept_profile $postgrest_write {
  default "http://{{ $postgrest }}.{{ .Release.Namespace }}.svc.cluster.local:3000";
}
{{- end }}

{{- end }}

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
    busy-timeout: 5s
    monitor-interval: 2s
    checkpoint-interval: 5m
    replica:
      type: s3
      bucket: {{ $root.Values.s3.bucket | quote }}
      path: {{ printf "%s/%s/catalog.sqlite" $root.Values.ducklake.catalogPath $schema | quote }}
      endpoint: {{ printf "%s://%s" (ternary "https" "http" (eq $root.Values.s3.useSsl "true")) (include "data-proxy.s3Endpoint" $root) | quote }}
      access-key-id: ${S3_ACCESS_KEY}
      secret-access-key: ${S3_SECRET_KEY}
      sync-interval: 5s
{{- end }}
{{- end }}
{{- end }}

{{- define "data-proxy.litestreamRestoreScript" -}}
#!/bin/sh
set -eu
umask 000
{{- $secrets := printf "%s/%s" .Values.ducklake.catalogLocalPath (include "data-proxy.duckdbSecretsDir" .) }}
mkdir -p {{ $secrets }}
chmod 777 {{ $secrets }}
{{- $schemas := .Values.sync.config.schemas }}
{{- range $schema, $_ := $schemas }}
(
  db={{ printf "%s/%s/catalog.sqlite" $.Values.ducklake.catalogLocalPath $schema }}
  dir={{ printf "%s/%s" $.Values.ducklake.catalogLocalPath $schema }}
  while true; do
    mkdir -p "$dir"
    chmod 777 "$dir"
    if [ -f "$db" ] && [ ! -f "${db}-txid" ]; then
      rm -f "$db" "${db}-wal" "${db}-shm"
    fi
    if [ -f "$db" ]; then
      chmod 666 "$db" "${db}-txid"
      exec litestream restore -f -config /projected/litestream-read.yaml "$db"
    fi
    litestream restore -if-replica-exists -f -config /projected/litestream-read.yaml "$db" || true
    sleep 5
  done
) &
{{- end }}
wait
{{- end }}

{{- define "data-proxy.postgresHome" -}}
/var/lib/postgresql/data
{{- end }}

{{- define "data-proxy.duckdbSecretsDir" -}}
duckdb-secrets
{{- end }}

{{- define "data-proxy.cnpgCatalogPodPatch" -}}
{{- $volume := dict
  "name" "ducklake-catalogs"
  "persistentVolumeClaim" (dict
    "claimName" (printf "%s-catalog-reader" (include "data-proxy.fullname" .))
    "readOnly" false
  )
-}}
{{- $mount := dict
  "name" "ducklake-catalogs"
  "mountPath" .Values.ducklake.catalogLocalPath
  "readOnly" false
-}}
{{- $secretsMount := dict
  "name" "ducklake-catalogs"
  "mountPath" (printf "%s/.duckdb/stored_secrets" (include "data-proxy.postgresHome" .))
  "subPath" (include "data-proxy.duckdbSecretsDir" .)
  "readOnly" false
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
      "command" (list "sh" "-c" "psql -U postgres -d data-proxy -tAc \"SELECT duckdb.raw_query('LOAD ducklake; LOAD sqlite')\" > /dev/null 2>&1 || true")
    )
  )
-}}
{{- list
  (dict "op" "add" "path" "/spec/volumes/-" "value" $volume)
  (dict "op" "add" "path" "/spec/volumes/-" "value" $tmpfsVolume)
  (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" $mount)
  (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" $secretsMount)
  (dict "op" "add" "path" "/spec/containers/0/volumeMounts/-" "value" $tmpfsMount)
  (dict "op" "add" "path" "/spec/containers/0/lifecycle" "value" $postStart)
  | toJson
-}}
{{- end }}

{{- define "data-proxy.appEnv" -}}
- name: PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.dbSecretName" . }}
      key: password
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
- name: DUCKLAKE_MAX_COMPACTED_FILES
  value: {{ .Values.ducklake.maxCompactedFiles | quote }}
- name: DUCKLAKE_REWRITE_DELETE_THRESHOLD
  value: {{ .Values.ducklake.rewriteDeleteThreshold | quote }}
- name: SYNC_CONFIG_PATH
  value: /config/sync.json
- name: PROXY_CACHE_REDIS_DB
  value: {{ .Values.proxy.cacheRedisDb | quote }}
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
- name: AUTH_POSTGREST_ROLE
  value: {{ .Values.auth.authenticatorRole | quote }}
- name: KUBERNETES_NAMESPACE
  value: {{ .Release.Namespace | quote }}
- name: POSTGREST_DEPLOYMENTS
  value: {{ include "data-proxy.postgrestDeployments" . | quote }}
- name: POSTGREST_ROLLOUT_TIMEOUT_SECONDS
  value: "300"
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: {{ .Values.gcp.mountPath | quote }}
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
  mountPath: {{ dir .Values.gcp.mountPath }}
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
  "max_parallel_workers_per_gather" "2"
  "duckdb.max_temp_directory_size" "2GB"
  "random_page_cost" "1.1"
  "effective_io_concurrency" "200"
  "max_connections" "200"
  "max_wal_size" "4GB"
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
