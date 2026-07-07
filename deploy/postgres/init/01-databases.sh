#!/usr/bin/env bash
# Runs once as the postgres superuser on first cluster init (mounted at
# /docker-entrypoint-initdb.d). Provisions the zammad/agent roles + databases
# that live alongside chatwoot's own database (created via POSTGRES_DB), and
# enables pgvector in the chatwoot database. Written to be idempotent enough
# to survive being re-run against an already-initialized data dir.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	DO
	\$\$
	BEGIN
	   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'zammad') THEN
	      CREATE ROLE zammad LOGIN PASSWORD '${ZAMMAD_DB_PASSWORD}';
	   END IF;
	END
	\$\$;

	DO
	\$\$
	BEGIN
	   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agent') THEN
	      CREATE ROLE agent LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
	   END IF;
	END
	\$\$;

	SELECT 'CREATE DATABASE zammad OWNER zammad'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'zammad')\gexec

	SELECT 'CREATE DATABASE agent OWNER agent'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent')\gexec
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
