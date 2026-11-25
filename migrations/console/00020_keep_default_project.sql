-- +goose Up
-- The schemas of an old install belong to the project default, so no token deletes
-- it, also a role with grant_all. The delete policy of console.projects gets one more
-- condition. The Down part restores the policy of 00009.
DROP POLICY delete_scope ON console.projects;
CREATE POLICY delete_scope ON console.projects FOR DELETE
USING (rbac.can('delete_config') AND name <> 'default' AND rbac.mine(NULL, name));

-- +goose Down
DROP POLICY delete_scope ON console.projects;
CREATE POLICY delete_scope ON console.projects FOR DELETE
USING (rbac.can('delete_config') AND rbac.mine(NULL, name));
