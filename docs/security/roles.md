# Roles

`rbac.toml` defines four roles, and each inherits the permissions of the roles that it lists. The identity provider and the database use the same names. A token has one role.

```mermaid
{{#include ../diagrams/role-inheritance.mmd}}
```

The configuration below defines the permissions, roles and inheritance. mdBook includes it directly from `rbac.toml`.

```toml
{{#include ../../helm/files/config/rbac.toml}}
```

`dp_policy` is for an API that registers its own access policies. `dp_admin` runs schemas, and every role is limited to the schemas in its claim. A user has one role. A person who needs both console and access policy rights takes `dp_admin`.

## How the rules are tested

The Python tests in `sync/tests/unit/test_templates_roles.py` run the `load_rbac` template on the real test server with the real `rbac.toml`.

They compare `rbac.can()` for every role and permission with a reference that the test computes itself. Hypothesis tests do the same for random rules files and random names. The end-to-end suite in `k6/scenarios/` tests the console with real tokens (`console.js` and `roles.js`) on the k3d cluster. It doesn't run in CI, because it needs the cluster. Run it before you merge a change to a permission, a role, an access policy, a view or the console schema (`devenv shell -- nu scripts/cluster.nu k6 e2e`).
