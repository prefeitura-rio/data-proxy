
{{- /* Return the environment of the sync engine: the database, Redis, S3 and DuckLake settings, and the Google credentials. */ -}}
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
- name: KUBERNETES_NAMESPACE
  value: {{ .Release.Namespace | quote }}
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: {{ .Values.gcp.mountPath | quote }}
{{- end }}
{{- end }}

{{- /* Return the volume of the sync configuration ConfigMap. */ -}}
{{- define "data-proxy.syncConfigVolume" -}}
- name: sync-config
  configMap:
    name: {{ include "data-proxy.fullname" . }}-sync
{{- end }}

{{- /* Return the read-only mount of the sync configuration at `/config`. */ -}}
{{- define "data-proxy.syncConfigVolumeMount" -}}
- name: sync-config
  mountPath: /config
  readOnly: true
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

{{- /* Return the pod spec of the `configure-db` Job. It waits for S3, and then runs the script with the SQL templates. */ -}}
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

{{- /* Return the pod spec of the `provision` Job. It waits for the configuration, and then makes the database match it. It takes `root`. */ -}}
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

{{- /* Return a Job that runs once for each Helm revision. It takes `root`, `name` and `podSpec`. */ -}}
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

{{- /* Return a suspended CronJob that holds a Job template, so that a Job starts from it on demand. It takes `root`, `name`, `deadline` and `podSpec`. */ -}}
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
