
{{- /* Return the name of the Secret that has the OIDC client secret and the cookie secret of the console. */ -}}
{{- define "data-proxy.consoleSecretName" -}}
{{- default (printf "%s-console" (include "data-proxy.fullname" .)) .Values.console.oidc.existingSecret }}
{{- end }}

{{- /* Return the public origin of the console. It fails when `ingress.host` is empty. */ -}}
{{- define "data-proxy.consoleOrigin" -}}
{{- printf "%s://%s" .Values.ingress.scheme (required "ingress.host is required when the console is enabled" .Values.ingress.host) }}
{{- end }}

{{- /* Return the public origin of the notebook site. */ -}}
{{- define "data-proxy.notebooksOrigin" -}}
{{- printf "%s://%s" .Values.ingress.scheme .Values.notebooks.host }}
{{- end }}

{{- /* The Content Security Policy of the notebook origin. notebooks/csp.ts builds the same string for the browser test. */ -}}
{{- define "data-proxy.notebooksCsp" -}}
{{- printf "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' 'wasm-unsafe-eval' blob:; worker-src 'self' blob:; connect-src 'self' blob: data: %s; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; frame-ancestors %s" .Values.console.storage.endpoint (include "data-proxy.consoleOrigin" .) }}
{{- end }}
