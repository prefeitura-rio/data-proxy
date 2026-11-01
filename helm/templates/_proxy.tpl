
{{- define "data-proxy.nginxConfigBody" -}}
{{- $root := .root -}}
{{- replace "__LARGE_CLIENT_HEADER_BUFFERS__" (toString $root.Values.proxy.largeClientHeaderBuffers) ($root.Files.Get "files/config/nginx.conf") -}}
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
