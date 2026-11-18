-- Read-only aggregate inventory. No identity/contact/credential payloads.
-- Run on the authenticated NeoApps production branch, never a guessed project.
-- Owner/backup designation is NOT inferable from these roles.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT 'users' AS section, jsonb_build_object(
  'total', count(*), 'active', count(*) FILTER (WHERE is_active),
  'stored_grandmasters', count(*) FILTER (WHERE role='grandmaster'),
  'active_nonvendor_grandmasters', count(*) FILTER (WHERE is_active AND role='grandmaster'
    AND NOT EXISTS (SELECT 1 FROM neoscorpion_vendor_accounts v WHERE v.user_id=users.id)),
  'active_masters', count(*) FILTER (WHERE is_active AND role='master')) AS counts
FROM users
UNION ALL SELECT 'app_grants', jsonb_agg(x) FROM (
  SELECT app_code,status,is_active,role,count(*) AS records
  FROM portal_app_accesses GROUP BY app_code,status,is_active,role) x
UNION ALL SELECT 'gateway_grants', jsonb_agg(x) FROM (
  SELECT gateway_id,status,is_active,count(*) AS records
  FROM gateway_memberships GROUP BY gateway_id,status,is_active) x
UNION ALL SELECT 'node_grants', jsonb_agg(x) FROM (
  SELECT n.code,r.role,r.is_active,count(*) AS records
  FROM gateway_node_roles r JOIN neo_nodes n ON n.id=r.node_id
  GROUP BY n.code,r.role,r.is_active) x
UNION ALL SELECT 'vendors', jsonb_build_object('accounts',count(*),
  'missing_ups_id',count(*) FILTER(WHERE NULLIF(trim(u.employee_id),'') IS NULL))
FROM neoscorpion_vendor_accounts v JOIN users u ON u.id=v.user_id;

WITH evidence AS (
  SELECT n.code, r.role AS explicit_rank, r.is_active AS explicit_active,
    CASE WHEN r.id IS NULL THEN COALESCE(a.role,u.role)
      WHEN r.is_active THEN r.role ELSE NULL END AS effective_rank
  FROM users u CROSS JOIN gateways g CROSS JOIN neo_nodes n
  JOIN gateway_memberships m ON m.user_id=u.id AND m.gateway_id=g.id
  LEFT JOIN portal_app_accesses a ON a.user_id=u.id AND a.app_code='neogateway'
  LEFT JOIN gateway_node_roles r ON r.gateway_membership_id=m.id AND r.node_id=n.id
  WHERE u.is_active AND g.is_active AND n.is_active AND m.is_active AND m.status='approved'
    AND (a.id IS NULL OR a.is_active AND a.status='approved')
    AND NOT EXISTS(SELECT 1 FROM neoscorpion_vendor_accounts v WHERE v.user_id=u.id)
)
SELECT 'node_projection_comparison' AS section, jsonb_build_object(
  'scope_pairs',count(*), 'current_allowed',count(effective_rank),
  'explicit_allowed',count(*) FILTER(WHERE explicit_active),
  'missing_explicit',count(*) FILTER(WHERE explicit_rank IS NULL),
  'differences',count(*) FILTER(WHERE effective_rank IS DISTINCT FROM
    CASE WHEN explicit_active THEN explicit_rank ELSE NULL END)) AS counts FROM evidence
UNION ALL SELECT 'effective_node_admins', jsonb_agg(x) FROM (
  SELECT code,effective_rank,count(*) AS records FROM evidence
  WHERE effective_rank IN ('master','grandmaster') GROUP BY code,effective_rank) x
UNION ALL SELECT 'unmapped_fallbacks', jsonb_agg(x) FROM (
  SELECT code,effective_rank,count(*) AS records FROM evidence
  WHERE explicit_rank IS NULL GROUP BY code,effective_rank) x;

SELECT permission_key, minimum_role FROM permission_rules ORDER BY permission_key;
ROLLBACK;
