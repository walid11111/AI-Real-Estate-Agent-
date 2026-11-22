🎯 Implementation Groups
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 1: Database Foundation (3 files)        Done        
  
  Purpose: Add email outbox + lead summary storage
  
  Files:
  1. backend/app/models/email_outbox.py (NEW)
    - EmailOutbox model with all fields
    - Status enum, dedupe logic ready
    - Relationships if needed
  

  
  2. backend/alembic/versions/006_add_email_outbox_summary.py (NEW)
    - Add leads.summary column (Text, nullable)
    - Create email_outbox table (12 columns)
    - Indexes on dedupe_key, status, next_attempt_at
    - Update app/models/__init__.py exports
  
  3. backend/app/models/__init__.py (UPDATE)
    - Add EmailOutbox to exports
  
  Verification:
  - alembic upgrade head works
  - alembic downgrade -1 works
  - No lint errors
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 2: Configuration & Email Client (2 files)               Start
  
  Purpose: Resend API integration + settings
  
  Files: 4. backend/app/core/config.py (UPDATE)
  - Add 7 email settings (RESEND_API_KEY, EMAIL_FROM, etc.)
  - Add properties: sales_team_list, email_test_recipients_list
  - No secrets printed in logs
  
  5. backend/app/services/email_resend.py (NEW)
    - is_configured() -> bool
    - send_email(to, subject, html, text) -> str (Resend message_id)
    - Uses httpx (already in dependencies)
    - Raises EmailError on failure
    - 10s timeout
  
  Verification:
  - is_configured() returns False without API key
  - No network calls yet (unit test with mock)
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 3: Email Templates (8 files)
  
  Purpose: Transactional email content (HTML + Plain Text)
  
  Files: 6. backend/app/templates/email/qualified_lead.html (NEW) 7. backend/app/templates/email/qualified_lead.txt
  (NEW) 8. backend/app/templates/email/viewing_booked.html (NEW) 9. backend/app/templates/email/viewing_booked.txt
  (NEW) 10. backend/app/templates/email/handoff.html (NEW) 11. backend/app/templates/email/handoff.txt (NEW) 12.
  backend/app/templates/email/appointment_confirmation.html (NEW) 13.
  backend/app/templates/email/appointment_confirmation.txt (NEW)
  
  Template Format: string.Template with $variable
  - Inline CSS for HTML
  - Professional real estate branding
  - No internal IDs to customers
  
  Verification:
  - All 8 files render with sample data
  - No syntax errors in templates
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 4: Notification Service (1 file)
  
  Purpose: Email queue + outbox processor + worker
  
  Files: 14. backend/app/services/notifications.py (NEW)
  - queue_email() - Create outbox row with dedupe
  - queue_qualified_lead_email(db, lead)
  - queue_viewing_booked_emails(db, appointment) (2 emails: sales + customer)
  - queue_handoff_email(db, handoff, lead)
  - process_outbox(db, limit=20) -> dict - Send pending emails
  - run_email_worker() - Background async loop
  - retry_email(db, outbox_id)
  - Recipient logic (test mode redirect, skip if no email)
  - Exponential backoff (1m, 5m, 15m, 1h)
  
  Verification:
  - Queue creates rows
  - Dedupe prevents duplicates
  - Process marks sent/failed correctly
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 5: Summary Service (1 file)
  
  Purpose: AI-powered lead summaries
  
  Files: 15. backend/app/services/summary.py (NEW)
  - build_facts(db, lead) -> dict - Extract lead data (NO PII)
  - generate_ai_fields(facts) -> tuple[str, str] - AI customer_interest + next_action
  - build_summary(db, lead) -> str - ASCII plain text summary
  - save_summary(db, lead, text)
  - Fallback on LLM timeout/error
  - Uses existing LLMClient (no new provider)
  
  Verification:
  - Facts never contain phone/email/whatsapp/name
  - Summary saved to leads.summary
  - Fallback text when LLM fails
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 6: Handoff Service (1 file)
  
  Purpose: Human agent transfer orchestration
  
  Files: 16. backend/app/services/handoff.py (NEW)
  - request_handoff(db, conversation, reason, source) -> HandoffResult
  - Steps:
    1. Ensure lead exists (get_or_create_lead)
    2. Raise status to at least HOT
    3. Generate + save summary
    4. Create Handoff record
    5. Queue handoff email
    6. Return fixed message
  
  - Safe to call twice (dedupe via handoff:{id})
  
  Verification:
  - Creates lead if missing
  - Sets status >= HOT
  - Saves summary
  - Queues email once

  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  GROUP 7: Integration Updates (6 files)
  
  Purpose: Connect new services to existing code

  Files: 17. backend/app/services/booking.py (UPDATE)
  - After appointment committed: queue_viewing_booked_emails()
  - Wrapped in try/except (never blocks booking)
  
  18. backend/app/services/leads.py OR lead_status.py (UPDATE)
  - When status becomes QUALIFIED:
    - Build + save summary
    - Queue qualified lead email
  
  - All wrapped in try/except with dedupe
  
  19. backend/app/agent/tools/notify.py (UPDATE - REPLACE STUB)
  - Call request_handoff() instead of stub
  - Return real handoff_id and message
  - Make lead_id optional, add conversation_id
  
  20. backend/app/agent/tools/history.py (UPDATE)
  - Support lookup by customer_id, conversation_id, phone, email, whatsapp_id
  - Include appointments + lead summary in output
  
  21. backend/app/agent/loop.py (UPDATE)
  - Inject conversation_id into params for notify_sales_team + get_customer_history
  - Keep existing SSE handoff event
  
  22. backend/app/agent/prompts.py (UPDATE)
  - Add rules: never claim to be human, exact handoff reply, no internal IDs
  
  Verification:
  - Booking still works (Phase 5 tests pass)
  - Email rows created after booking
  - Qualified lead triggers email
  - Tools work with conversation_id injection
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 8: API Endpoints (2 files)
  
  Purpose: Manual handoff trigger
  
  Files: 23. backend/app/routers/chat.py (UPDATE)
  - Add POST /chat/handoff endpoint
  - Body: {"session_token": str, "reason": str | None}
  - Returns: {"message": "...", "handoff_id": int, "lead_id": int}
  - 401 invalid token, 400 not active
  
  24. backend/app/main.py (UPDATE)
  - On startup: create_task(run_email_worker()) if enabled
  - On shutdown: Cancel worker task
  
  Verification:
  - Endpoint works via Swagger
  - Worker starts with server
  - Worker stops cleanly
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 9: Schema Updates (1 file)
  
  Purpose: Extend tool schemas
  
  Files: 25. backend/app/schemas/tools.py (UPDATE)
  - Update NotifySalesTeamInput: make lead_id optional, add conversation_id
  - Update GetCustomerHistoryInput: add phone, email, whatsapp_id fields
  - Update GetCustomerHistoryOutput: add appointments, summary fields
  
  Verification:
  - Schemas validate correctly
  - No breaking changes to existing tools
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  GROUP 10: Tests (5 files)
  
  Purpose: Comprehensive testing (no network calls)
  
  Files: 26. backend/tests/conftest.py (UPDATE)
  - Set EMAIL_WORKER_ENABLED=false before app import
  - Mock email provider globally
  
  27. backend/tests/test_notifications.py (NEW)
  - Queue creates rows
  - Dedupe prevents duplicates
  - Process marks sent/failed
  - Backoff scheduling
  - Test mode redirects
  - Max attempts -> failed
  - Missing API key -> failed with reason
  
  28. backend/tests/test_summary.py (NEW)
  - Facts match DB (no PII)
  - AI fields parsed
  - Fallback on LLM error
  - Summary saved to leads.summary
  
  29. backend/tests/test_handoff.py (NEW)
  - Tool creates lead, sets status, saves summary, queues email
  - Endpoint 401 without token
  - Double call doesn't duplicate email
  
  30. backend/tests/test_booking.py (UPDATE)
  - Assert 2 email rows created after booking
  - Booking succeeds even if email fails
  
  Verification:
  - All ~220+ tests pass
  - No flaky tests
  - Coverage of new code
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  📊 Phase 6 Summary
  
  ┌───────┬───────────────┬───────┬─────┬─────────┐
  │ Group │ Purpose       │ Files │ New │ Updated │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 1     │ Database      │ 3     │ 2   │ 1       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 2     │ Email Client  │ 2     │ 1   │ 1       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 3     │ Templates     │ 8     │ 8   │ 0       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 4     │ Notifications │ 1     │ 1   │ 0       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 5     │ Summary       │ 1     │ 1   │ 0       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 6     │ Handoff       │ 1     │ 1   │ 0       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 7     │ Integration   │ 6     │ 0   │ 6       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 8     │ API           │ 2     │ 0   │ 2       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 9     │ Schemas       │ 1     │ 0   │ 1       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ 10    │ Tests         │ 5     │ 3   │ 2       │
  ├───────┼───────────────┼───────┼─────┼─────────┤
  │ TOTAL │ Phase 6       │ 30    │ 17  │ 13      │
  └───────┴───────────────┴───────┴─────┴─────────┘
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  ✅ Zero Duplication Strategy
  
  Reusing from existing code:
  - ✅ Handoff model (already exists)
  - ✅ Notification model (already exists)
  - ✅ Lead/Customer models (already exists)
  - ✅ LLMClient (existing, reuse for summary)
  - ✅ Tool registry (existing infrastructure)
  - ✅ Conversation service (existing)
  - ✅ Booking service (existing, just add email)
  - ✅ httpx (already in dependencies)
  
  Not rebuilding:
  - ❌ No new database models for handoff/notification
  - ❌ No new LLM client
  - ❌ No new dependencies (except maybe resend SDK? No, use httpx!)
  - ❌ No changes to Phase 5 booking logic
  
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  
  🎯 Implementation Order
  
  Day 1: Groups 1-3 (Foundation)
  - Database + Email client + Templates
  
  Day 2: Groups 4-6 (Core Services)
  - Notifications + Summary + Handoff
  
  Day 3: Groups 7-8 (Integration)
  - Update existing services + API
  
  Day 4: Groups 9-10 (Testing)
  - Schemas + Full test suite