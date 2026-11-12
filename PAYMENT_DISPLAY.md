# Daily customer receipts

Customer Ledger consolidates incoming payment records by customer and receipt date. Cash Drawer consolidates incoming cash receipts by customer and drawer date. Transfers to the same account also consolidate by customer and date into one outgoing row, separate from incoming cash. Different destinations, outgoing expenses, manual float entries, returned-cheque reversals and balance adjustments remain separate. Different customers are identified by database ID, even if they have identical names.

Use **View individual payments**, **View receipt records** or **View transfer records** to inspect the underlying records. Details stay collapsed until opened. Editing from the ledger edits one original payment, not the consolidated amount. Its linked cash-drawer entries, bank transfer, customer balance and bill status are updated together.

## Payment dates

- New bill: choose **Cash payment date** in the Payment step. Cheques retain their individual **Received** dates.
- Record Payment on an existing bill: choose **Actual payment date**. This is also the cheque receipt date if paying by cheque.
- Settle Balance: choose **Settlement date** for cash. Each cheque has its own received date.

Cash receipts use that selected date in both Payment.paid_at and CashDrawer.txn_date; a later payment no longer inherits the original bill date. Dates are interpreted in the application's local timezone.

Grouping is done in memory before pagination and running-balance calculation. It never merges or deletes Payment records, bill allocations, or audit history. Ledger exports and cash-drawer Excel use the same consolidated presentation. Totals and underlying financial report sources remain unchanged.

Migrations 0039 and 0040 add a nullable payment link to cash-drawer entries and link unambiguous historical generated receipts. They do not change historical amounts, dates or balances. Ambiguous historical links are left untouched rather than guessed; entries with a known bill still consolidate by that bill's customer. Historical manual entries without customer identity remain separate.

Regression tests cover receipt and outgoing-transfer grouping, different dates/customers/destinations, payment date validation, bill settlement and excess credit, individual edits, pagination, exports, migration matching, rendered scripts and permissions.
