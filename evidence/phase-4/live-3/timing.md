# Phase 4 live timings

Run dbd07456.

## -
- start 1.547s timed_out=False
- start 1.562s timed_out=False
- start 1.531s timed_out=False
- deepseek 1.828s timed_out=False
- deepseek 2.094s timed_out=False
- deepseek 2.203s timed_out=False
- deepseek 1.0s timed_out=False
- deepseek 0.953s timed_out=False
- deepseek 1.609s timed_out=False
- deepseek 1.313s timed_out=False
- deepseek 3.766s timed_out=False
- deepseek 3.704s timed_out=False
- composer-2.5 43.656s timed_out=False
- deepseek 0.953s timed_out=False
- deepseek 1.157s timed_out=False
- npm-test 0.329s timed_out=False
- test 0.25s timed_out=False
- composer-2.5 45.813s timed_out=False
- deepseek 1.39s timed_out=False
- composer-2.5 51.094s timed_out=False
- deepseek 1.156s timed_out=False
- deepseek 3.657s timed_out=False
- deepseek 2.641s timed_out=False
- pip-install-pytest 2.015s timed_out=False
- pip-install-pytest 5.296s timed_out=False
- pytest 0.047s timed_out=False
- pytest 0.047s timed_out=False

## Results
- add-notifications-tests failed No files changed.
- add-admin-tests failed No files changed.
- document-client-types failed No files changed.

Wall 134.4s sequential 196.8s speedup 1.46x isolation=PASS

## Phases

Workers spawned together at 13:38:12Z with empty upstream lists. The last worker exited at 13:39:22Z, so the fleet window is about 70s.

- document-client-types: composer-2.5 43.656s, task 57.1s, exited 13:39:09Z. npm-test 0.329s failed. test 0.250s passed.
- add-notifications-tests: composer-2.5 45.813s, task 70.0s, exited 13:39:22Z. pip-install-pytest 5.296s failed. pytest 0.047s failed.
- add-admin-tests: composer-2.5 51.094s, task 69.7s, exited 13:39:22Z. pip-install-pytest 2.015s failed. pytest 0.047s failed.

The sequential estimate is the sum of those three overlapping task durations. Wall time also includes the scan, plan, and router before 13:38:06Z, about 64s that does not add to the sequential sum. 196.8 / 134.4 = 1.46. The fleet alone is 196.8 / 70, about 2.8x.
