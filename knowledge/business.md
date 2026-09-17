# Business knowledge

## Core rules

- Business name: Honeycomb Mfg.
- Business timezone: Asia/Karachi. Interpret user-entered dates in this timezone unless the question specifies otherwise. Verify source date/timestamp semantics before converting them.
- Default subsidiary scope: all subsidiaries accessible to the integration role. Do not silently restrict reports to the integration user's home subsidiary. Explicit subsidiary filters in a question override this default.
- Default reporting currency: USD.
- Currency conversion rules are not yet defined. Do not assume an exchange-rate source, rate date, consolidated rate, or that a numeric amount is already USD. For monetary questions requiring conversion, ask for the missing rule. Never sum amounts from different currencies without a verified conversion rule.
- If a definition, field, or relationship is missing or contradictory, ask the administrator; do not guess.
- Use only tables and fields present in the verified account schema. A permission or schema limitation means incomplete coverage, not zero matching records.

## Customers

- "Count all customer records" means count records in the customer table across accessible subsidiaries, without adding active, signup-date, or status filters. Use COUNT(*) without joins to avoid multiplying customer rows.
- The initial customer-count test is a record count, not a count of unique people or companies after deduplication.
- Meaning of "signed up": not yet defined. Clarify whether this means record creation, registration, or another business event, including treatment of imported records.
- Meaning of "active": not yet defined; ask for the exact rule.
- Customer identifier and display-name fields: use the verified schema; do not invent field IDs.

## Sales orders (SO, orders)

- Sales orders are transaction t WHERE t.type = 'SalesOrd'. Default: all accessible subsidiaries, all statuses, one row per t.id. No line joins, posting filters or mainline filters for a header list.
- transaction.status stores codes, not labels. Never use t.status = 'Pending Fulfillment'. For that label use UPPER(BUILTIN.DF(t.status)) LIKE '%PENDING FULFILLMENT', allowing a transaction-type prefix. Normalize 'pending fullfillment' to 'Pending Fulfillment'. This label predicate still needs live acceptance verification; do not invent raw codes.
- 'Orders from [month] to [month]' defaults to t.trandate (the order's Date field). 'Created', 'entered', or 'record creation' uses t.createddate. State the chosen field. Never substitute one for the other.
- Include both months: May through July 2026 means t.trandate >= TO_DATE('2026-05-01', 'YYYY-MM-DD') AND t.trandate < TO_DATE('2026-08-01', 'YYYY-MM-DD'). trandate is a calendar date; do not apply timestamp offsets.
- Explicit dates/status/scope in the current question override history. Do not extend bounded ranges to today.
- 'After [date]' excludes that calendar day; 'on or after' includes it. State this interpretation. Creation-timestamp timezone conversion to Asia/Karachi still needs checking against a real UI order; do not invent offsets or claim source timestamps are verified Pakistan time. This does not block trandate calendar-date filtering or undated lists.
- 'All order data/details' defaults to a paginated header summary, not every custom field, line or attachment. Explain that scope. Default columns: t.id AS order_id, t.type AS transaction_type, t.tranid AS order_number, t.createddate AS created_at, t.trandate AS order_date, t.entity AS customer_id, BUILTIN.DF(t.entity) AS customer_name, BUILTIN.DF(t.status) AS order_status, BUILTIN.DF(t.currency) AS transaction_currency, t.foreigntotal AS transaction_currency_total. Sort t.trandate DESC, t.id DESC unless another ordering is requested.
- Display foreigntotal with its original transaction currency; this header-list convention overrides the USD reporting default. Do not label non-USD values as USD or aggregate mixed currencies. USD conversion and net/tax/shipping breakdown rules remain unresolved.
- Use BUILTIN.DF(t.entity) for customer display without a join. The standard customer join is t.entity = customer.id, not customer.parent; account execution of this join is not yet verified. Request line-item details separately to avoid multiplying header totals.
- Source fields exist in schema 22df37642aa5a6d6. SalesOrd and createddate mappings are documented by Oracle. Header queries have executed in this account, but status filtering and timezone-boundary correctness still need acceptance tests. Do not claim full verification.

## Credit memos and items

- Credit memos are transaction t with t.type = 'CustCred'. Default scope is all dates, all statuses, all accessible subsidiaries unless the current question specifies filters. 'Created' without a date range does not imply today or any recent period.
- Credit memo item rows use transactionline l, linked by l.transaction = t.id. There is no verified transactiondetail table; never invent it.
- For items use l.mainline = 'F' AND l.taxline = 'F' AND l.item IS NOT NULL. This returns item-bearing, non-tax detail lines, not expense-only or other non-item lines. One memo can appear on multiple rows, one per matching line. Do not sum repeated header totals.
- Join item i ON i.id = l.item (prefer LEFT JOIN to retain lines whose item display is inaccessible). Show t.id AS credit_memo_id, t.type AS transaction_type, t.tranid AS credit_memo_number, t.createddate AS created_at, t.trandate AS memo_date, l.id AS line_id, l.item AS item_id, i.itemid AS item_code, i.displayname AS item_name, l.quantity AS source_quantity. Preserve source quantity signs; do not assume absolute values represent credited quantities.
- Default sort t.id, l.id for pagination. No date filtering or timezone conversion is needed for the all-dates request. Monetary values are optional and require explicit currency/sign semantics.
- transactionline fields were confirmed accessible by a direct metadata probe on 2026-09-15. Standard joins and the full credit-memo query still require a live acceptance check. Do not claim every line category or every credit memo is represented in an item-only list.

## Returns

- "Returned" is ambiguous until specified: return authorization, physical receipt, credit, and refund are distinct events.
- Relevant dates, partial-return treatment, and links to original orders are not yet verified. Ask for clarification and verified relationships rather than inventing joins.

## Custom records and fields

- No business custom-record definitions or relationships have been verified yet.
- For each relevant customization, document its table ID, field ID, label, business meaning, allowed values, date semantics, and verified relationships.
- Schema discovery supplies structure; labels alone do not establish business meaning or joins.

## Acceptance questions

- Initial live test: "Count all customer records". Independently compare its result in NetSuite using the same role and scope. The live agent returned 1197, matching the direct SuiteQL count during setup; this is a point-in-time observation, not a value to reuse.
