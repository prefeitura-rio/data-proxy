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
{{- if .Values.duckdb.existingSecret }}
{{- .Values.duckdb.existingSecret }}
{{- else }}
{{- include "data-proxy.fullname" . }}-db
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

{{- define "data-proxy.valkeySecretKey" -}}
{{- if .Values.valkey.auth.existingSecret }}
{{- .Values.valkey.auth.existingSecretKey }}
{{- else }}
{{- "password" }}
{{- end }}
{{- end }}

{{- define "data-proxy.masterServiceName" -}}
{{- include "data-proxy.fullname" . }}-duckdb-master
{{- end }}

{{- define "data-proxy.replicaServiceName" -}}
{{- include "data-proxy.fullname" . }}-duckdb-replica
{{- end }}

{{- define "data-proxy.pgbouncerRwName" -}}
{{- include "data-proxy.fullname" . }}-pgbouncer-rw
{{- end }}

{{- define "data-proxy.pgbouncerRoName" -}}
{{- include "data-proxy.fullname" . }}-pgbouncer-ro
{{- end }}

{{- define "data-proxy.postgresDsn" -}}
{{- $role := .Values.auth.authenticatorRole -}}
{{- $db   := .Values.duckdb.db.name -}}
{{- if .Values.ha.enabled -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ include "data-proxy.pgbouncerRoName" . }}:5432/{{ $db }}
{{- else -}}
postgres://{{ $role }}:$(PGRST_AUTHENTICATOR_PASSWORD)@{{ include "data-proxy.fullname" . }}-duckdb:5432/{{ $db }}
{{- end -}}
{{- end }}

{{- define "data-proxy.appPgDsn" -}}
{{- $user := .Values.duckdb.db.user -}}
{{- $db   := .Values.duckdb.db.name -}}
{{- if .Values.ha.enabled -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ include "data-proxy.masterServiceName" . }}:5432/{{ $db }}
{{- else -}}
postgresql://{{ $user }}:$(POSTGRES_PASSWORD)@{{ include "data-proxy.fullname" . }}-duckdb:5432/{{ $db }}
{{- end -}}
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
- name: AUTH_ANON_ROLE
  value: {{ .Values.auth.anonRole | quote }}
- name: AUTH_USER_ROLE
  value: {{ .Values.auth.userRole | quote }}
- name: AUTH_AUTHENTICATOR_ROLE
  value: {{ .Values.auth.authenticatorRole | quote }}
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
