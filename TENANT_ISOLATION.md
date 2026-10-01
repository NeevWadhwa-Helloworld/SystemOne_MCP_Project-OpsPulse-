# Tenant isolation

Managed resources are owned by the authenticated user (`resources.owner_user_id`).
Non-admin API callers can only list, inspect, run, mutate, or delete their own
resources and their associated checks, reports, approvals, and webhook
references. The bootstrap `admin` is deliberately global so it can operate the
local installation and administer all tenants; ordinary users are never
granted that visibility.

Existing databases are migrated conservatively. Legacy rows with no owner are
hidden from non-admin callers and are assigned to the configured bootstrap
admin when the application starts. Do not remove or bypass that bootstrap
configuration to expose legacy rows.

MCP runs over local stdio and has no ambient HTTP session. Every managed
resource MCP tool therefore requires an explicit `caller_identity` (username
or user id); missing or unknown identities fail closed. Callers must obtain
that identity through the normal authenticated API session.
