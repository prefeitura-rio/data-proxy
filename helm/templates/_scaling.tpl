
{{- /* Return the default KEDA triggers: CPU at 70% and memory at 90% of the request. */ -}}
{{- define "data-proxy.defaultResourceTriggers" -}}
- type: cpu
  metricType: Utilization
  metadata:
    value: "70"
- type: memory
  metricType: Utilization
  metadata:
    value: "90"
{{- end }}

{{- /* The proxy scales on CPU only. Its pods use a fixed amount of memory, so a memory trigger holds two pods after a spike. */ -}}
{{- define "data-proxy.cpuTrigger" -}}
- type: cpu
  metricType: Utilization
  metadata:
    value: "70"
{{- end }}

{{- /* Return the minimum number of CNPG instances. It's at least 2: one primary and one standby. */ -}}
{{- define "data-proxy.cnpgMinInstances" -}}
{{- max (.Values.cnpg.autoscaling.minReplicaCount | int) 2 -}}
{{- end }}

{{- /* Return the maximum number of CNPG instances. It's never below the minimum. */ -}}
{{- define "data-proxy.cnpgMaxInstances" -}}
{{- max (.Values.cnpg.autoscaling.maxReplicaCount | int) (include "data-proxy.cnpgMinInstances" . | int) -}}
{{- end }}

{{- /* Return the CPU limit of an instance in whole cores, rounded up. */ -}}
{{- define "data-proxy.cnpgCpuLimitCores" -}}
{{- $cpu := .Values.cnpg.resources.limits.cpu | toString -}}
{{- if hasSuffix "m" $cpu -}}
{{- divf (trimSuffix "m" $cpu | float64) 1000.0 | ceil | int -}}
{{- else -}}
{{- $cpu | float64 | ceil | int -}}
{{- end -}}
{{- end }}

{{- /* Return the target of active sessions for each instance: the cores divided by the DuckDB threads, with a minimum of 1. */ -}}
{{- define "data-proxy.cnpgSessionsPerInstance" -}}
{{- $threads := include "data-proxy.duckdbThreads" . | trimAll "\"" | float64 -}}
{{- max 1 (divf (include "data-proxy.cnpgCpuLimitCores" . | float64) $threads | ceil | int) -}}
{{- end }}

{{- /* Return the default KEDA trigger of the cluster: the active PostgREST sessions for each instance. */ -}}
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

{{- /* Return the triggers of a block, or the default triggers when the block sets none. It takes `triggers` and `default`. */ -}}
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
