-- Local development database setup for Reyvo.
--
-- Run as a PostgreSQL superuser (it will prompt for the postgres password):
--   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f scripts\setup_local_db.sql
--
-- This creates/refreshes the development-only role and database used by
-- DATABASE_URL=postgresql+psycopg://reyvo:reyvo@localhost:5432/reyvo
-- Do not use these credentials in production.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reyvo') THEN
    ALTER ROLE reyvo WITH LOGIN PASSWORD 'reyvo';
  ELSE
    CREATE ROLE reyvo WITH LOGIN PASSWORD 'reyvo';
  END IF;
END
$$;

SELECT 'CREATE DATABASE reyvo OWNER reyvo'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'reyvo')\gexec

ALTER DATABASE reyvo OWNER TO reyvo;
GRANT ALL PRIVILEGES ON DATABASE reyvo TO reyvo;
