
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

{{- define "data-proxy.s3CredentialsEnv" -}}
- name: S3_BUCKET
  value: {{ .bucket | quote }}
- name: S3_ENDPOINT
  value: {{ .endpoint | quote }}
- name: S3_USE_SSL
  value: {{ .useSsl | quote }}
- name: S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .secret | quote }}
      key: S3_ACCESS_KEY
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .secret | quote }}
      key: S3_SECRET_KEY
{{- end }}

{{- define "data-proxy.s3Env" -}}
{{- include "data-proxy.s3CredentialsEnv" (dict
  "bucket" .Values.s3.bucket
  "endpoint" (include "data-proxy.s3Endpoint" .)
  "useSsl" .Values.s3.useSsl
  "secret" (include "data-proxy.s3SecretName" .)
) }}
{{- end }}

{{- define "data-proxy.s3BootstrapSettingsEnv" -}}
- name: S3_BOOTSTRAP_TIMEOUT
  value: {{ .Values.s3.bootstrap.timeout | quote }}
- name: S3_BOOTSTRAP_RETRY
  value: {{ .Values.s3.bootstrap.retry | quote }}
{{- if .Values.gcp.existingSecret }}
- name: GOOGLE_APPLICATION_CREDENTIALS
  value: {{ .Values.gcp.mountPath | quote }}
{{- end }}
{{- end }}

{{- define "data-proxy.s3BootstrapEnv" -}}
{{- include "data-proxy.s3Env" . }}
{{ include "data-proxy.s3BootstrapSettingsEnv" . }}
{{- end }}

{{- define "data-proxy.s3BootstrapVolume" -}}
- name: s3-bootstrap
  configMap:
    name: {{ include "data-proxy.fullname" . }}-s3-bootstrap
    defaultMode: 0555
{{- end }}

{{- define "data-proxy.s3WaitContainer" -}}
- name: {{ .name }}
  image: {{ .root.Values.jobs.image | quote }}
  {{- include "data-proxy.containerSecurity" (dict "root" .root "component" "jobs") | nindent 2 }}
  command: [nu, /s3-bootstrap/ensure-s3-bucket.nu, --wait]
  env:
    {{- .env | nindent 4 }}
  volumeMounts:
    - name: s3-bootstrap
      mountPath: /s3-bootstrap
      readOnly: true
    {{- include "data-proxy.gcpVolumeMount" .root | nindent 4 }}
{{- end }}

{{- define "data-proxy.s3WaitInitContainer" -}}
{{- include "data-proxy.s3WaitContainer" (dict
  "name" "wait-for-s3"
  "root" .
  "env" (include "data-proxy.s3BootstrapEnv" .)
) }}
{{- end }}

{{- define "data-proxy.backupS3WaitInitContainer" -}}
{{- $backup := .Values.jobs.backup.s3 -}}
{{- $credentials := include "data-proxy.s3CredentialsEnv" (dict
  "bucket" $backup.bucket
  "endpoint" $backup.endpointURL
  "useSsl" "true"
  "secret" $backup.existingSecret
) -}}
{{- include "data-proxy.s3WaitContainer" (dict
  "name" "wait-for-backup-s3"
  "root" .
  "env" (print $credentials "\n" (include "data-proxy.s3BootstrapSettingsEnv" .))
) }}
{{- end }}
