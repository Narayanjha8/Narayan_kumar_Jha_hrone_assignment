# Architectural Decisions

1. **Why FastAPI?**
   - Chosen for its high performance, native support for async operations, automatic OpenAPI documentation generation, and straightforward Pydantic integration for input validation.

2. **Why MongoDB?**
   - Flexible document schema fits attendance logs with nested history and audit trails. Powerful aggregation pipelines handle complex grouping, ranking, and departmental summaries efficiently.

3. **Handling of Indexes & Performance:**
   - Idempotent unique compound indexes (`emp_code`, `date`) are created on startup to prevent race conditions during duplicate punch-ins and ensure fast lookups (`IXSCAN`).

4. **Timezone & Date Handling:**
   - Timestamps are stored in UTC with explicit timezone conversions (IST) to ensure accurate shift calculations, grace periods, and late-minute determinations regardless of server location.

5. **Audit Trail & Regularization:**
   - Manual corrections via `PATCH` append an immutable history log containing previous vs. new field values, the user who modified it, and the reason, ensuring full traceability.