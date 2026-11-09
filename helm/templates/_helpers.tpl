
{{- /* Return the chart name, cut to 63 characters. */ -}}
{{- define "data-proxy.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- /* Return the name that prefixes every resource of the release, cut to 63 characters. `fullnameOverride` and `nameOverride` change it. */ -}}
{{- define "data-proxy.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- /* Return the standard labels of a resource: the chart, the name, the instance and the manager. */ -}}
{{- define "data-proxy.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "data-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- /* Return the labels that select the pods of this release. */ -}}
{{- define "data-proxy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "data-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- /* Return the name of the service account. The chart creates it only when `serviceAccount.create` is true. */ -}}
{{- define "data-proxy.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- .Values.serviceAccount.name | default (include "data-proxy.fullname" .) }}
{{- else }}
{{- .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- /* Return the volume of the Google credentials, when `gcp.existingSecret` is set. */ -}}
{{- define "data-proxy.gcpVolume" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp
  secret:
    secretName: {{ .Values.gcp.existingSecret }}
{{- end }}
{{- end }}

{{- /* Return the mount of the Google credentials volume, when `gcp.existingSecret` is set. */ -}}
{{- define "data-proxy.gcpVolumeMount" -}}
{{- if .Values.gcp.existingSecret }}
- name: gcp
  mountPath: {{ dir .Values.gcp.mountPath }}
  readOnly: true
{{- end }}
{{- end }}

{{- /* The pod settings. They are the same for every pod. */ -}}
{{- define "data-proxy.podSecurity" -}}
securityContext:
  {{- toYaml .Values.securityContext.pod | nindent 2 }}
{{- end }}

{{- /* The pod settings of the sync workers: the normal ones, and the group of the sync user as `fsGroup` when a spill volume is on, so that the user can write to a disk-backed volume. */ -}}
{{- define "data-proxy.syncPodSecurity" -}}
{{- $pod := deepCopy .Values.securityContext.pod -}}
{{- if .Values.sync.duckdb.tempVolume.enabled -}}
{{- $_ := set $pod "fsGroup" (dig "components" "sync" "runAsGroup" 10001 .Values.securityContext | int64) -}}
{{- end -}}
securityContext:
  {{- toYaml $pod | nindent 2 }}
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
