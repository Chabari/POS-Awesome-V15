# HANDOVER: wrongful "not enough stock" blocks on the till

**Status (2026-11-17 09:40 EAT):** DEPLOYED by the user (build 09:34:51 EAT, bench restart 09:35:14 EAT,
asset version `1794897271147`). Verified served bundle contains the fix. Awaiting a real till confirmation.
Note: server clock and DB are CET (EAT minus 2 h).

## The problem
Victoria Biashara till refused to sell KNEATED SWADDLE (`VBS-00462`) although the
server had 13 in `BIASHARA STREET SHOP - VKB`. No invoice ever reached the server;
the refusal came from the till's saved quantity.

Why the till's saved quantity goes stale:
1. `useItemsIntegration.js` returns a no-op `update_items_details` from `setup()`. In
   Vue 3 setup bindings win over component methods, so ItemsSelector's real
   `update_items_details` (the 30 s background quantity refresh) never runs.
   Present since commit `b3075f8`. **Not fixed here** (approach 2, separate change).
2. The delta sync (`refreshModifiedItems`) is keyed on `Item.modified`; Stock
   Entries / receipts don't change it.
3. Only the full reload 1 s after each completed sale corrects quantities.
4. Scan and tap paths block using the saved number without asking the server.

## The fix (approach 1: check with the server before refusing)
When the till's saved number says the item is short, ask the server once for the
live warehouse quantity (`posawesome.posawesome.api.items.get_available_qty`),
write it onto the item and into `stockCoordinator`, then let the existing gates
decide with live numbers. Offline, a timeout (4 s), or an error -> old behaviour.
Batch items are skipped (unchanged behaviour). Final server check at submit is
untouched.

Files:
- `frontend/src/posapp/utils/stock.js` - `needsLiveStockCheck`, `fetchLiveStockQty`
- `frontend/src/posapp/components/pos/ItemsSelector.vue` - `confirmStockWithServer`,
  called in `add_item` (tap) and `addScannedItemToInvoice` (scan)
- `frontend/tests/stockConfirm.spec.js` - unit tests

## Progress log
- [x] Diagnosis (read-only on live site)
- [x] Code + unit tests (`cd frontend && npx vitest run` -> 42 passed)
- [x] Test build to scratchpad (`npx vite build --outDir <scratch>`) -> OK; live dist untouched
- [x] Endpoint check: get_available_qty(VBS-00462, BIASHARA STREET SHOP - VKB) -> 13.0
- [x] Deployed by user 09:35 EAT (yarn build + bench restart). Affects Neema too (shared app).
- [x] Post-deploy: nginx serves loader `v=1794897271147` -> posawesome.js with `live-stock-check`;
      gunicorn started after the build; no Error Log entries since restart.
- [x] Till simulation `frontend/tests/staleStockTill.spec.js` (real ItemsSelector methods, fake
      server): tap + scan sell VBS-00462 saved at 0 when server has 13; still block at 0, when
      cart+request > live, and offline. With the fix disabled the two "sells" tests fail. 48/48 pass.
- [ ] Each till must reload the POS page once (tabs open before 09:35 still run old code).
- [ ] Live check: Stock Entry for a test item, scan it straight away on Biashara

## Rollback
`git checkout -- <files above>` then rebuild (`cd frontend && yarn build`) and `bench restart`.
