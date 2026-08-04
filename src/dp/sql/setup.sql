INSTALL httpfs;
LOAD httpfs;
INSTALL bigquery FROM community;
LOAD bigquery;
INSTALL postgres;
LOAD postgres;
CREATE SECRET (
    TYPE gcs,
    KEY_ID '${key_id}',
    SECRET '${secret_key}',
    ENDPOINT '${endpoint}',
    URL_STYLE 'path',
    USE_SSL ${use_ssl}
);
CREATE MACRO bq_table(t) AS TABLE bigquery_scan(t)
