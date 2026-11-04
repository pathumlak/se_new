# Manager task permissions

Sign in as a super admin, open **Manager Permissions**, select the manager, check the tasks they should perform, and click **Save permissions**. Changes take effect on the manager's next request without requiring a new login. Required view permissions are included and audited when saved. Unchecking all tasks and saving revokes access to business screens; the manager can still use their profile and sign out.

Use individual View, Create, Edit, Delete and payment tasks for limited access. A section's **Manage** permission includes every action in that section. To remove one action from a manager who has Manage, uncheck Manage and select only the individual tasks they need.

Useful combinations:

- Production entry: Create production. View production is added automatically.
- Product maintenance: Create products and/or Edit products. Creating products also includes permission to enter the initial price. Changing existing prices requires Edit product prices.
- Billing: Create bills; Record customer payments for paid sales; Manage partial payments if needed; Use existing balance in billing to consume customer credit. Edit product prices is required for a price override.
- Collections: Settle bills or Record customer payments; Manage partial payments for payments below the outstanding amount.
- Reports: View reports. Exports also need Export reports and permission to view the corresponding source data.

Permission templates copy the selected tasks to a manager while keeping existing grants. Editing a template does not retroactively change managers. Duplicate template names display a validation message.

User/permission administration, audit administration, and the raw database console stay restricted to super admins. Nonfunctional raw-table permission options are no longer offered. Existing legacy row permissions are accepted only for their matching operation, never as permission to perform all operations on a module.

The dashboard permission includes its business summaries. View customers includes customer details and balances; View customer ledgers separately controls ledger access. Financial notification and cash drawer summaries are filtered by the manager's access.

Verification: 30 targeted tests cover assignment, revocation, manager isolation, task routes, legacy grant isolation, navigation, exports, read-only POST protection, balance edits, payment and credit checks, and templates. These tests use a temporary test database. No real manager grants are changed during installation.
