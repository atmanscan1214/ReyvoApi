-- Reyvo PostgreSQL row-level security policies.
--
-- Run this AFTER `alembic upgrade head`. The application sets the tenant with:
--
--   SELECT set_config('app.tenant_id', '<tenant-uuid>', true);
--
-- The `true` makes the setting transaction-local. RLS is defense in depth: the
-- service layer also filters every query by tenant_id.
--
-- IMPORTANT: connect as an application role that does NOT have BYPASSRLS and is
-- not the table owner (owners bypass RLS unless FORCE ROW LEVEL SECURITY is set).
-- The statements below use FORCE to close that gap.

DO $$
DECLARE
  table_name text;
  tenant_scoped_tables text[] := ARRAY[
    'memberships',
    'email_accounts',
    'companies',
    'contacts',
    'contact_lists',
    'contact_list_members',
    'segments',
    'campaigns',
    'campaign_steps',
    'campaign_enrollments',
    'messages',
    'replies',
    'opportunities',
    'tasks',
    'imports',
    'subscriptions',
    'usage_counters'
  ];
BEGIN
  FOREACH table_name IN ARRAY tenant_scoped_tables LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I '
      'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) '
      'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      table_name
    );
  END LOOP;
END $$;

-- Suppressions and audit logs can contain global (tenant_id IS NULL) rows.
DROP POLICY IF EXISTS tenant_isolation ON suppressions;
ALTER TABLE suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppressions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON suppressions
  USING (
    tenant_id IS NULL
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  )
  WITH CHECK (
    tenant_id IS NULL
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  );

DROP POLICY IF EXISTS tenant_isolation ON audit_logs;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_logs
  USING (
    tenant_id IS NULL
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  )
  WITH CHECK (
    tenant_id IS NULL
    OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  );

-- Plans, users, and webhook_events are intentionally global. Tenant access to
-- them is mediated by the service layer (and by tenant-scoped membership rows).
