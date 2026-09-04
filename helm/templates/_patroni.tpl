{{/*
This file defines the Patroni cluster configuration as a Helm template.
*/}}
{{- define "data-proxy.patroniConfig" -}}
scope: ${PATRONI_SCOPE}
name: ${PATRONI_NAME}

restapi:
  listen: 0.0.0.0:8008
  connect_address: ${PATRONI_POD_IP}:8008

kubernetes:
  namespace: ${PATRONI_NAMESPACE}
  labels:
    app.kubernetes.io/name: {{ include "data-proxy.name" . }}
    app.kubernetes.io/instance: {{ .Release.Name }}
  use_endpoints: true

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    postgresql:
      parameters:
        shared_preload_libraries: pg_duckdb
        max_wal_senders: {{ .Values.ha.patroni.maxWalSenders }}
        wal_keep_size: {{ .Values.ha.patroni.walKeepSize | quote }}
        hot_standby: "on"
  initdb:
    - encoding: UTF8
    - data-checksums
  pg_hba:
    - host replication {{ .Values.ha.patroni.replicationUsername }} 0.0.0.0/0 md5
    - host all all 0.0.0.0/0 md5
  post_init: /scripts/post-init.sh

postgresql:
  listen: 0.0.0.0:5432
  connect_address: ${PATRONI_POD_IP}:5432
  data_dir: /var/lib/postgresql/data/pgdata
  pgpass: /tmp/pgpass
  authentication:
    replication:
      username: {{ .Values.ha.patroni.replicationUsername }}
      password: ${PATRONI_REPLICATION_PASSWORD}
    superuser:
      username: ${PATRONI_SUPERUSER_USERNAME}
      password: ${PATRONI_SUPERUSER_PASSWORD}
  parameters:
    shared_preload_libraries: pg_duckdb
{{- end }}
