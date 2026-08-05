INSTALL httpfs;
LOAD httpfs;
INSTALL bigquery FROM community;
LOAD bigquery;
INSTALL postgres_scanner;
LOAD postgres_scanner;
CREATE SECRET (
    TYPE s3,
    KEY_ID '${key_id}',
    SECRET '${secret_key}',
    ENDPOINT '${endpoint}',
    URL_STYLE 'path',
    USE_SSL ${use_ssl},
    REGION 'us-east-1'
)
