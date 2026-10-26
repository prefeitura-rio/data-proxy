
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

{{- define "data-proxy.cnpgMinInstances" -}}
{{- max (.Values.cnpg.autoscaling.minReplicaCount | int) 2 -}}
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
    query: {{ printf "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND backend_type = 'client backend' AND usename = '%s'" .Values.auth.servingRole | quote }}
    targetQueryValue: {{ include "data-proxy.cnpgSessionsPerInstance" . | quote }}
{{- end }}

{{- define "data-proxy.triggers" -}}
{{- if gt (len (.triggers | default list)) 0 -}}
{{ toYaml .triggers }}
{{- else -}}
{{ .default }}
{{- end -}}
{{- end }}

{{- /*
One ScaledObject per workload whose count a scaler owns, so Helm never sets a count that KEDA also writes.
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
spec:
  scaleTargetRef:
    {{- toYaml .target | nindent 4 }}
  minReplicaCount: {{ .min }}
  maxReplicaCount: {{ .max }}
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
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
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

{{- define "data-proxy.s3SecretName" -}}
{{- .Values.s3.existingSecret | default (printf "%s-s3" (include "data-proxy.fullname" .)) }}
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

{{- define "data-proxy.s3Env" -}}
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
{{- end }}

{{- define "data-proxy.s3BootstrapEnv" -}}
{{- include "data-proxy.s3Env" . }}
- name: S3_BOOTSTRAP_TIMEOUT
  value: {{ .Values.s3.bootstrap.timeout | quote }}
- name: S3_BOOTSTRAP_RETRY
  value: {{ .Values.s3.bootstrap.retry | quote }}
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: {{ .Values.gcp.mountPath | quote }}
{{- end }}
{{- end }}

{{- define "data-proxy.s3BootstrapVolume" -}}
- name: s3-bootstrap
  configMap:
    name: {{ include "data-proxy.fullname" . }}-s3-bootstrap
    defaultMode: 0555
{{- end }}

{{- define "data-proxy.s3WaitInitContainer" -}}
- name: wait-for-s3
  image: {{ .Values.jobs.image | quote }}
  {{- include "data-proxy.containerSecurity" (dict "root" $ "component" "jobs") | nindent 2 }}
  command: [nu, /s3-bootstrap/ensure-s3-bucket.nu, --wait]
  env:
    {{- include "data-proxy.s3BootstrapEnv" . | nindent 4 }}
  volumeMounts:
    - name: s3-bootstrap
      mountPath: /s3-bootstrap
      readOnly: true
    {{- include "data-proxy.gcpVolumeMount" . | nindent 4 }}
{{- end }}

{{- define "data-proxy.backupS3WaitInitContainer" -}}
- name: wait-for-backup-s3
  image: {{ .Values.jobs.image | quote }}
  {{- include "data-proxy.containerSecurity" (dict "root" $ "component" "jobs") | nindent 2 }}
  command: [nu, /s3-bootstrap/ensure-s3-bucket.nu, --wait]
  env:
    - name: S3_BUCKET
      value: {{ .Values.jobs.backup.s3.bucket | quote }}
    - name: S3_ENDPOINT
      value: {{ .Values.jobs.backup.s3.endpointURL | quote }}
    - name: S3_USE_SSL
      value: "true"
    - name: S3_ACCESS_KEY
      valueFrom:
        secretKeyRef:
          name: {{ .Values.jobs.backup.s3.existingSecret | quote }}
          key: S3_ACCESS_KEY
    - name: S3_SECRET_KEY
      valueFrom:
        secretKeyRef:
          name: {{ .Values.jobs.backup.s3.existingSecret | quote }}
          key: S3_SECRET_KEY
    - name: S3_BOOTSTRAP_TIMEOUT
      value: {{ .Values.s3.bootstrap.timeout | quote }}
    - name: S3_BOOTSTRAP_RETRY
      value: {{ .Values.s3.bootstrap.retry | quote }}
    {{- if .Values.gcp.existingSecret }}
    - name: GOOGLE_APPLICATION_CREDENTIALS
      value: {{ .Values.gcp.mountPath | quote }}
    {{- end }}
  volumeMounts:
    - name: s3-bootstrap
      mountPath: /s3-bootstrap
      readOnly: true
    {{- include "data-proxy.gcpVolumeMount" . | nindent 4 }}
{{- end }}

{{- define "data-proxy.redisSecretName" -}}
{{- .Values.redis.existingSecret | default (printf "%s-redis" (include "data-proxy.fullname" .)) }}
{{- end }}

{{- define "data-proxy.redisPasswordKey" -}}
{{- .Values.redis.passwordKey | default "REDIS_PASSWORD" }}
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

{{- define "data-proxy.postgresWriteDsn" -}}
{{- $role := .Values.auth.servingRole -}}
{{- $db := .Values.cnpg.db.name -}}
{{- $cluster := include "data-proxy.fullname" . -}}
postgres://{{ $role }}:$(PGRST_PASSWORD)@{{ $cluster }}-rw:5432/{{ $db }}
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

{{- define "data-proxy.nginxConfigBody" -}}
{{- $root := .root -}}
{{- replace "__LARGE_CLIENT_HEADER_BUFFERS__" (toString $root.Values.proxy.largeClientHeaderBuffers) ($root.Files.Get "files/config/nginx.conf") -}}
{{- end }}

{{- /* nginx resolves a name without the search domains of the pod, so a name with no dot gets the namespace of the release. */ -}}
{{- define "data-proxy.redisHost" -}}
{{- $host := required "redis.host is required: the proxy stores its answers in this Redis" .Values.redis.host -}}
{{- if contains "." $host -}}
{{- $host -}}
{{- else -}}
{{- printf "%s.%s.svc.cluster.local" $host .Release.Namespace -}}
{{- end -}}
{{- end }}

{{- define "data-proxy.proxyEnv" -}}
{{- $postgrest := printf "%s-postgrest" (include "data-proxy.fullname" .) -}}
{{- $address := printf "http://%s.%s.svc.cluster.local:3000" $postgrest .Release.Namespace -}}
- name: PROXY_CACHE_TTL
  value: {{ toString .Values.proxy.cacheTtl | quote }}
- name: PROXY_MAX_BODY
  value: {{ toString .Values.proxy.maxCacheBodyBytes | quote }}
- name: PROXY_REDIS_HOST
  value: {{ include "data-proxy.redisHost" . | quote }}
- name: PROXY_REDIS_PORT
  value: {{ toString .Values.redis.port | quote }}
- name: PROXY_REDIS_DATABASE
  value: {{ toString .Values.proxy.cacheRedisDb | quote }}
- name: PROXY_REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.redisSecretName" . }}
      key: {{ include "data-proxy.redisPasswordKey" . }}
- name: PROXY_POSTGREST
  value: {{ $address | quote }}
- name: PROXY_MAX_INFLIGHT
  value: {{ toString .Values.proxy.maxInflight | quote }}
- name: PROXY_CONFIG_URL
  value: {{ printf "%s/rpc/config" $address | quote }}
- name: PROXY_CONFIG_REFRESH_SECONDS
  value: {{ toString .Values.proxy.configRefreshSeconds | quote }}
{{- end }}

{{- define "data-proxy.nginxProxyConfig" -}}
{{ include "data-proxy.nginxConfigBody" (dict "root" .) }}
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

{{- define "data-proxy.appEnv" -}}
- name: PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.dbSecretName" . }}
      key: password
- name: PG_DATABASE_URL
  value: {{ include "data-proxy.appPgDsn" . | quote }}
{{ include "data-proxy.redisConfigEnv" . }}
{{ include "data-proxy.s3Env" . }}
- name: S3_SCRATCH_PREFIX
  value: {{ .Values.s3.scratchPrefix | quote }}
{{- if and .Values.console.enabled .Values.console.storage.perSchemaIdentities }}
- name: S3_IDENTITIES
  value: "true"
{{- end }}
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
- name: PROXY_CACHE_REDIS_DB
  value: {{ .Values.proxy.cacheRedisDb | quote }}
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
- name: DUCKDB_MEMORY_LIMIT
  value: {{ .Values.sync.duckdb.memoryLimit | quote }}
- name: DUCKDB_THREADS
  value: {{ .Values.sync.duckdb.threads | quote }}
- name: SYNC_QUEUE_CONCURRENCY
  value: {{ .Values.sync.queueConcurrency | quote }}
- name: AUTH_ANON_ROLE
  value: {{ .Values.auth.anonRole | quote }}
- name: AUTH_USER_ROLE
  value: {{ .Values.auth.userRole | quote }}
- name: AUTH_POSTGREST_ROLE
  value: {{ .Values.auth.servingRole | quote }}
- name: KUBERNETES_NAMESPACE
  value: {{ .Release.Namespace | quote }}
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: {{ .Values.gcp.mountPath | quote }}
{{- end }}
{{- end }}

{{- define "data-proxy.gcpVolume" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp
  secret:
    secretName: {{ .Values.gcp.existingSecret }}
{{- end }}
{{- end }}

{{- define "data-proxy.gcpVolumeMount" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp
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

{{- /* The keys of the SQL ConfigMap are flat (dir_file.sql); items give them back their directory. */ -}}
{{- define "data-proxy.sqlTemplateItems" -}}
{{- range $path, $_ := .Files.Glob "files/templates/*.sql" }}
- key: {{ base $path }}
  path: {{ base $path }}
{{- end }}
{{- range $path, $_ := .Files.Glob "files/templates/**/*.sql" }}
- key: {{ base (dir $path) }}_{{ base $path }}
  path: {{ base (dir $path) }}/{{ base $path }}
{{- end }}
{{- end }}

{{- define "data-proxy.configureDbPodSpec" -}}
restartPolicy: OnFailure
automountServiceAccountToken: false
{{- include "data-proxy.podSecurity" $ | nindent 0 }}
initContainers:
  {{- include "data-proxy.s3WaitInitContainer" . | nindent 2 }}
containers:
  - name: configure-db
    image: {{ .Values.jobs.image | quote }}
    {{- include "data-proxy.containerSecurity" (dict "root" $ "component" "jobs") | nindent 4 }}
    imagePullPolicy: {{ .Values.sync.imagePullPolicy }}
    command: [nu, /scripts/configure-db.nu]
    workingDir: /scripts
    env:
      - name: PASSWORD
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.dbSecretName" . }}
            key: password
      - name: PG_DATABASE_URL
        value: {{ printf "postgresql://%s@%s-rw:5432/%s" .Values.cnpg.db.user (include "data-proxy.fullname" .) .Values.cnpg.db.name | quote }}
      {{- include "data-proxy.pgEnv" . | nindent 6 }}
      - name: DUCKLAKE_ADMIN_URL
        value: {{ printf "postgresql://%s@%s-rw:5432/ducklake" .Values.cnpg.db.user (include "data-proxy.fullname" .) | quote }}
      - name: DUCKDB_PASSWORD
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.duckdbSecretName" . }}
            key: password
      - name: SERVING_PASSWORD
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.servingSecretName" . }}
            key: password
      - name: SIGNER_PASSWORD
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.signerSecretName" . }}
            key: password
      - name: AUTH_SIGNER_ROLE
        value: {{ .Values.auth.signerRole | quote }}
      - name: AUTH_ANON_ROLE
        value: {{ .Values.auth.anonRole | quote }}
      - name: AUTH_USER_ROLE
        value: {{ .Values.auth.userRole | quote }}
      - name: AUTH_SERVING_ROLE
        value: {{ .Values.auth.servingRole | quote }}
      - name: DBOS_APPLICATION_NAME
        value: {{ .Values.sync.dbos.applicationName | quote }}
      {{- include "data-proxy.s3Env" . | nindent 6 }}
      - name: SQL_TEMPLATE_DIR
        value: /templates
    volumeMounts:
      - name: configure-db-script
        mountPath: /scripts
        readOnly: true
      - name: configure-db-sql
        mountPath: /templates
        readOnly: true
volumes:
  {{- include "data-proxy.s3BootstrapVolume" . | nindent 2 }}
  {{- include "data-proxy.gcpVolume" . | nindent 2 }}
  - name: configure-db-script
    configMap:
      name: {{ include "data-proxy.fullname" . }}-configure-db-script
      defaultMode: 0555
  - name: configure-db-sql
    configMap:
      name: {{ include "data-proxy.fullname" . }}-configure-db-sql
      defaultMode: 0444
      items:
        {{- include "data-proxy.sqlTemplateItems" . | nindent 8 }}
{{- end }}

{{- define "data-proxy.provisionPodSpec" -}}
{{- $root := .root -}}
restartPolicy: OnFailure
automountServiceAccountToken: false
{{- include "data-proxy.podSecurity" $root | nindent 0 }}
initContainers:
  - name: wait-for-config
    image: {{ $root.Values.jobs.image | quote }}
    {{- include "data-proxy.containerSecurity" (dict "root" $root "component" "jobs") | nindent 4 }}
    imagePullPolicy: {{ $root.Values.sync.imagePullPolicy }}
    command: [nu, /scripts/wait-for-relation.nu]
    workingDir: /scripts
    env:
      - name: RELATION
        value: console.schemas
      {{- include "data-proxy.pgEnv" $root | nindent 6 }}
    volumeMounts:
      - name: scripts
        mountPath: /scripts
        readOnly: true
containers:
  - name: provision
    image: {{ $root.Values.jobs.image | quote }}
    {{- include "data-proxy.containerSecurity" (dict "root" $root "component" "jobs") | nindent 4 }}
    imagePullPolicy: {{ $root.Values.sync.imagePullPolicy }}
    command: [nu, /scripts/provision.nu]
    workingDir: /scripts
    env:
      - name: PASSWORD
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.dbSecretName" $root }}
            key: password
      - name: PG_DATABASE_URL
        value: {{ include "data-proxy.appPgDsn" $root | quote }}
      - name: AUTH_USER_ROLE
        value: {{ $root.Values.auth.userRole | quote }}
      - name: AUTH_SERVING_ROLE
        value: {{ $root.Values.auth.servingRole | quote }}
      - name: AUTH_SIGNER_ROLE
        value: {{ $root.Values.auth.signerRole | quote }}
      - name: PGUSER
        value: {{ $root.Values.cnpg.db.user | quote }}
      - name: DBOS_APP_SCHEMA
        value: {{ $root.Values.sync.dbos.appSchema | quote }}
      - name: AUTH_ANON_ROLE
        value: {{ $root.Values.auth.anonRole | quote }}
      - name: PGPASSWORD
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.dbSecretName" $root }}
            key: password
      - name: DUCKLAKE_ADMIN_URL
        value: {{ printf "postgresql://%s@%s-rw:5432/ducklake" $root.Values.cnpg.db.user (include "data-proxy.fullname" $root) | quote }}
      - name: DUCKLAKE_CATALOG_PATH
        value: {{ $root.Values.ducklake.catalogPath | quote }}
      - name: S3_BUCKET
        value: {{ $root.Values.s3.bucket | quote }}
      - name: S3_ENDPOINT
        value: {{ include "data-proxy.s3Endpoint" $root | quote }}
      - name: S3_USE_SSL
        value: {{ $root.Values.s3.useSsl | quote }}
      - name: AWS_ACCESS_KEY_ID
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.s3SecretName" $root }}
            key: S3_ACCESS_KEY
      - name: AWS_SECRET_ACCESS_KEY
        valueFrom:
          secretKeyRef:
            name: {{ include "data-proxy.s3SecretName" $root }}
            key: S3_SECRET_KEY
      {{- if $root.Values.gcp.existingSecret }}
      - name: GOOGLE_APPLICATION_CREDENTIALS
        value: {{ $root.Values.gcp.mountPath | quote }}
      {{- end }}
      - name: SYNC_CONFIG_PATH
        value: /config/sync.json
      {{- if .all }}
      - name: PROVISION_ALL
        value: "true"
      {{- end }}
      - name: SQL_TEMPLATE_DIR
        value: /templates
    volumeMounts:
      - name: scripts
        mountPath: /scripts
        readOnly: true
      - name: templates
        mountPath: /templates
        readOnly: true
      {{- include "data-proxy.syncConfigVolumeMount" $root | nindent 6 }}
      {{- include "data-proxy.gcpVolumeMount" $root | nindent 6 }}
volumes:
  - name: scripts
    configMap:
      name: {{ include "data-proxy.fullname" $root }}-configure-db-script
      defaultMode: 0555
  - name: templates
    configMap:
      name: {{ include "data-proxy.fullname" $root }}-configure-db-sql
      items:
        {{- include "data-proxy.sqlTemplateItems" $root | nindent 8 }}
  {{- include "data-proxy.syncConfigVolume" $root | nindent 2 }}
  {{- include "data-proxy.gcpVolume" $root | nindent 2 }}
{{- end }}

{{- define "data-proxy.revisionJob" -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "data-proxy.fullname" .root }}-{{ .name }}-{{ .root.Release.Revision }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    {{- include "data-proxy.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}
spec:
  backoffLimit: 3
  template:
    metadata:
      labels:
        {{- include "data-proxy.selectorLabels" .root | nindent 8 }}
        app.kubernetes.io/component: {{ .name }}
    spec:
      {{- .podSpec | nindent 6 }}
{{- end }}

{{- define "data-proxy.templateCronJob" -}}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "data-proxy.fullname" .root }}-{{ .name }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    {{- include "data-proxy.labels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .name }}-template
spec:
  suspend: true
  schedule: "0 0 1 1 *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: {{ .deadline }}
      ttlSecondsAfterFinished: 300
      template:
        metadata:
          labels:
            {{- include "data-proxy.selectorLabels" .root | nindent 12 }}
            app.kubernetes.io/component: {{ .name }}-template
        spec:
          {{- .podSpec | nindent 10 }}
{{- end }}

{{- define "data-proxy.consoleSecretName" -}}
{{- default (printf "%s-console" (include "data-proxy.fullname" .)) .Values.console.oidc.existingSecret }}
{{- end }}

{{- define "data-proxy.consoleOrigin" -}}
{{- printf "%s://%s" .Values.ingress.scheme (required "ingress.host is required when the console is enabled" .Values.ingress.host) }}
{{- end }}

{{- define "data-proxy.notebooksOrigin" -}}
{{- printf "%s://%s" .Values.ingress.scheme .Values.notebooks.host }}
{{- end }}

{{- /* The Content Security Policy of the notebook origin. notebooks/csp.ts builds the same string for the browser test. */ -}}
{{- define "data-proxy.notebooksCsp" -}}
{{- printf "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' 'wasm-unsafe-eval' blob:; worker-src 'self' blob:; connect-src 'self' blob: data: %s; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; frame-ancestors %s" .Values.console.storage.endpoint (include "data-proxy.consoleOrigin" .) }}
{{- end }}

{{- /* The pod settings. They are the same for every pod. */ -}}
{{- define "data-proxy.podSecurity" -}}
securityContext:
  {{- toYaml .Values.securityContext.pod | nindent 2 }}
{{- end }}

{{- /* The container settings of a component: the defaults, with the settings of the component on top. */ -}}
{{- define "data-proxy.containerSecurity" -}}
{{- $defaults := .root.Values.securityContext -}}
{{- $own := dig "components" .component (dict) $defaults -}}
securityContext:
  {{- mergeOverwrite (deepCopy $defaults.container) $own | toYaml | nindent 2 }}
{{- end }}

{{- /* A network peer: the pods of one component of this release. */ -}}
{{- define "data-proxy.podPeer" -}}
podSelector:
  matchLabels:
    {{- include "data-proxy.selectorLabels" .root | nindent 4 }}
    app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- /* A network peer: every pod of one namespace. */ -}}
{{- define "data-proxy.namespacePeer" -}}
namespaceSelector:
  matchLabels:
    kubernetes.io/metadata.name: {{ . }}
{{- end }}

{{- /* The from list of a rule that serves requests: the peers, then the extra peers of the values. */ -}}
{{- define "data-proxy.allowFrom" -}}
from:
  {{- concat .peers .root.Values.networkPolicy.extraPeers | toYaml | nindent 2 }}
{{- end }}

