
{{- /* Return the name of the Redis Secret. */ -}}
{{- define "data-proxy.redisSecretName" -}}
{{- .Values.redis.existingSecret | default (printf "%s-redis" (include "data-proxy.fullname" .)) }}
{{- end }}

{{- /* Return the key of the Redis password in the Redis Secret. */ -}}
{{- define "data-proxy.redisPasswordKey" -}}
{{- .Values.redis.passwordKey | default "REDIS_PASSWORD" }}
{{- end }}

{{- /* Return the variable `REDIS_WRITE` from the Redis Secret. */ -}}
{{- define "data-proxy.redisConfigEnv" -}}
- name: REDIS_WRITE
  valueFrom:
    secretKeyRef:
      name: {{ include "data-proxy.redisSecretName" . }}
      key: {{ .Values.redis.writeKey | default "REDIS_WRITE" }}
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
