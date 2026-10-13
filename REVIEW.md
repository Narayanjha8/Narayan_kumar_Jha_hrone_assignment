# Review of Starter Code

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Used a strict integer comparison (`> 10`) instead of checking total seconds, causing punch-ins at 10 minutes and 1 second to return 0 late minutes. | Punch in at 09:40:01 for a 09:30 shift and check `late_minutes`. | Updated the logic to check exact seconds (`> 600`) before converting to minutes. |
| 2 | `punch_in` / Startup | No unique compound index on `(emp_code, date)` and separate `find_one()` + `insert_one()` checks. | Concurrent punch-in requests for the same employee on the same day could create duplicate records. | Added idempotent startup indexes with unique constraints and handled `DuplicateKeyError`. |
| 3 | `list_employees` | Skip calculation used `page * page_size` instead of `(page - 1) * page_size`. | Requesting page 1 skipped the first page of results entirely. | Corrected the skip formula to `(page - 1) * page_size`. |

**Not a Defect:**
- Using environment variables for MongoDB connection strings was noted, but fallback values (`mongodb://localhost:27017`) were kept correctly for local testing.