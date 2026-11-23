-- AU voice agent: the database, its login and the vector extension (run 1 of 2).
--
-- Run once on the RDS / Aurora PostgreSQL 16 or 17 instance, as the master user, connected to the database "postgres":
--   psql "host=<RDS endpoint> dbname=postgres user=<master> sslmode=require" -v ON_ERROR_STOP=1 -f 01_bootstrap.sql
-- Then run 02_schema.sql in the new database.
--
-- Replace CHANGE_ME with a long random password and put the same password in the app's DATABASE_URL
-- (Secrets Manager secret au/brain):
--   postgresql://au_voice_app:<password>@<RDS endpoint>:5432/au_voice?sslmode=require

\set ON_ERROR_STOP on

-- 1. The app's own login. Not a superuser; it owns only its own database.
CREATE ROLE au_voice_app LOGIN PASSWORD 'CHANGE_ME' CONNECTION LIMIT 60;

-- 2. Let the master user act as that role (RDS's master is not a true superuser), so it can give it the database
--    and run 02_schema.sql on its behalf.
GRANT au_voice_app TO CURRENT_USER;

-- 3. One database for the app. Inside it: schema "public" (the app's data) and schema "corebank" (the mock bank).
CREATE DATABASE au_voice OWNER au_voice_app ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE au_voice FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE au_voice TO au_voice_app;

-- 4. pgvector (built into RDS PostgreSQL 16/17). Creating an extension needs the master user (rds_superuser).
\connect au_voice
CREATE EXTENSION IF NOT EXISTS vector;
