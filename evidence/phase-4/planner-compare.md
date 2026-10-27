# Planner comparison

The default planner stays `deepseek/deepseek-v4-pro`. This is one extra call, not a fleet run.

Run `55d0986a` measured scan 0.031s, baseline checks 2.671s, planner 44.484s, router 0.016s, fleet 79.297s. The planner is the pre-fleet time.

Strong planner in run 7792ae9b: 48.844s. Tasks: 3. plan guard: pass.
- add-notifications-tests: Add unit tests for notifications module; paths=['tests/test_notifications.py']; depends_on=[]; check_ids=['pytest']
- add-admin-tests: Add unit tests for admin module; paths=['tests/test_admin.py']; depends_on=[]; check_ids=['pytest']
- document-client-types: Document exported TypeScript client types; paths=['src/client/types.ts']; depends_on=[]; check_ids=['npm-test', 'test']

Flash planner: 17.860s. Tasks: 3. plan guard: pass.
- add-notifications-tests: Add unit tests for the notifications module; paths=['tests/test_notifications.py']; depends_on=[]; check_ids=['pytest']
- add-admin-tests: Add unit tests for the admin module; paths=['tests/test_admin.py']; depends_on=[]; check_ids=['pytest']
- document-client-types: Document exported TypeScript client types; paths=['src/client/types.ts']; depends_on=[]; check_ids=['test', 'npm-test']

