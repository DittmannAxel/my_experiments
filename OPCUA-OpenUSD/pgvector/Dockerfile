FROM pgvector/pgvector:pg16

# init.sql is auto-executed by the official postgres image entrypoint
# when the data directory is empty.
COPY init.sql /docker-entrypoint-initdb.d/01-init.sql
