# Customer-level outstanding settlements

Use **Settle Balance** on a customer account. The displayed outstanding amount comes from the customer's available balance. New settlements create one `CustomerSettlement` reference (`SET-…`) and one Payment per cash/bank leg or cheque. They never allocate money to previous bills or change those bills' paid amounts or statuses. Historical bill payments are retained unchanged.

Cash, cheque, bank transfer and mixed payments are supported. Partial payments use the entered amounts; existing partial-payment permissions still apply. Overpayments remain customer credit. Every submission requires a selected payment date, settlement note and payment notice. Bank receipts require an account, bank name and reference; cheques require their identifying details, amount and maturity date. Every cheque's received date matches the settlement date.

The ledger and drawer share the same settlement reference, note, notice, date and payment breakdown. Same-day receipts remain grouped by customer, with individual records available in expanded details. Cash receipts increase physical cash; bank and cheque entries are informational non-cash receipts. Cash explicitly moved to Senovka retains both cash-in and transfer-out legs. Drawer Excel and ledger exports retain the common reference and notes.

Pending, deposited and held cheques remain credited according to the existing application rules. Returned cheques reverse their credit. Their status transitions are audited. The previous/updated available balances on a receipt are snapshots at the moment of recording, including when the selected receipt date is backdated.

## Corrections and cancellation

Open the settlement reference in the ledger or drawer. **Correct settlement** requires a reason and atomically replaces the active receipt. **Cancel settlement** requires a reason and removes its current credit. Original settlements, payment-method records and audits remain available in history. Repeated submissions use a unique form token and cannot post the same receipt twice; repeated cancellations do not reverse money twice.

Corrections retain the status of cheques carried over from the original form, including returned cheques. Add a new cheque row when replacing an instrument. Editing/deleting an individual settlement cheque or payment through a legacy route redirects to the common settlement workflow.

Active accounting querysets exclude cancelled/corrected settlement components. `all_objects` on Payment, CashDrawer, CashTransfer and Cheque retains the complete audit history. New reporting code should use `objects` for live financial totals and use `all_objects` only for history.

Permissions reuse the existing tasks: `billing.settle` to create, `billing.partial_payments` to underpay an outstanding balance, `balances.view` to view receipts and `payments.edit` to correct or cancel them. Existing cheque-status permissions continue to apply.

## Deployment

Commit and push all changed files, including `0042_customer_account_settlements.py`, before pulling on the server. Back up the server database, then run the normal deployment with `python manage.py migrate`, `collectstatic` and service restart. There is no need to generate migrations on the server for this update. Migration 0042 only adds the settlement schema and non-cash categories; it does not rewrite historical financial records.

Validation: 95 focused tests passed across account settlements, daily consolidation, permission tasks, notifications, supplier payments and payment dates. UI checks confirmed a 50,000 cash + 60,000 bank + 30,000 cheque receipt totals 140,000 while physical cash increases by 50,000. The existing broader suite has legacy permission/expectation failures; comparison of 177 related tests against the unchanged Desktop checkout found the same failing test names before and after this update.
