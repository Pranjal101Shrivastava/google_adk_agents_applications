-- Seed tickets for the MCP Bug Assistant.
--
-- Chosen to exercise the behaviours the agent is supposed to get right:
--   * #1 and #8 are the *same bug worded differently* - semantic duplicate
--     detection should find #1 when someone reports #8's symptoms.
--   * #2 is a third-party library bug, so the right answer is a web/GitHub
--     search, not a new ticket.
--   * #4 is Resolved, so a new report of it should surface the fix.
--   * #6 is Blocked, which the agent must not confuse with In Progress.
--
--   psql "$DATABASE_URL" -f data/seed.sql

INSERT INTO tickets (title, description, assignee, priority, status, creation_time)
VALUES
  (
    'Checkout returns 500 on orders with more than 20 line items',
    'POST /api/v2/checkout fails with HTTP 500 when the cart contains more than '
    'about 20 line items. Logs show "ValueError: could not convert string to '
    'float" raised from svc/pricing/aggregate.py line 142 in compute_totals. '
    'Reproducible on staging with a 21-item cart. Smaller carts are unaffected.',
    'maya.okafor@example.com', 'P1 - High', 'In Progress', NOW() - INTERVAL '6 days'
  ),
  (
    'Intermittent connection reset from the payments SDK',
    'The payments SDK (vendor-pay 3.4.2) raises ConnectionResetError roughly '
    'once per 2000 calls. Stack trace bottoms out in '
    'site-packages/vendorpay/transport.py, so the fault appears to be in the '
    'library rather than our code. Retries succeed. Vendor changelog for 3.5.0 '
    'mentions a keep-alive fix that may be the same issue.',
    'dev.null@example.com', 'P2 - Medium', 'Open', NOW() - INTERVAL '12 days'
  ),
  (
    'Password reset emails are delivered to the spam folder',
    'Since the mail provider migration, password reset emails land in spam for '
    'Outlook and Hotmail recipients. SPF passes; DKIM alignment appears to be '
    'the problem. Affects every user on those providers, with no workaround '
    'other than contacting support.',
    'sam.rivera@example.com', 'P1 - High', 'Open', NOW() - INTERVAL '3 days'
  ),
  (
    'Mobile app crashes on launch for Android 13 users',
    'The app crashed immediately on launch on Android 13 due to a missing '
    'POST_NOTIFICATIONS runtime permission declaration. Fixed in release 4.2.1 '
    'by requesting the permission at runtime; verified on Pixel 6 and Galaxy '
    'S22. Root cause was in app/src/main/MainActivity.kt line 88.',
    'maya.okafor@example.com', 'P0 - Critical', 'Resolved', NOW() - INTERVAL '30 days'
  ),
  (
    'Search results omit items indexed in the last hour',
    'Newly created catalogue items do not appear in search for up to 60 '
    'minutes. The indexer runs on a cron schedule rather than reacting to '
    'change events, so the lag is by design but surprises users. Workaround is '
    'to wait for the next run.',
    'lee.chen@example.com', 'P2 - Medium', 'Open', NOW() - INTERVAL '20 days'
  ),
  (
    'Nightly export job fails with an out-of-memory error',
    'The nightly analytics export is killed by the OOM reaper when the dataset '
    'exceeds about 4 GB. Fixing it needs the streaming export work that is '
    'waiting on the data platform team, so this cannot proceed yet. No data '
    'loss: the previous export remains valid.',
    'lee.chen@example.com', 'P1 - High', 'Blocked', NOW() - INTERVAL '45 days'
  ),
  (
    'Dark mode toggle resets after a page reload',
    'The theme preference is stored in component state rather than persisted, '
    'so reloading the page reverts to light mode. Cosmetic, with an obvious '
    'workaround, but reported often enough to be worth fixing.',
    NULL, 'P3 - Low', 'Open', NOW() - INTERVAL '60 days'
  ),
  (
    'Cannot complete purchase with a large basket',
    'A customer reported that submitting an order with around two dozen '
    'products shows "something went wrong" and the order is never created. '
    'Works fine with a handful of products. Reported by three customers this '
    'week via support.',
    NULL, 'P2 - Medium', 'Open', NOW() - INTERVAL '1 day'
  ),
  (
    'API rate limit headers are missing on 429 responses',
    'When the gateway returns HTTP 429 it omits Retry-After and '
    'X-RateLimit-Reset, so clients cannot back off correctly and hammer the '
    'endpoint. Affects every API consumer. Regression: the headers were present '
    'before the gateway upgrade in release 4.1.',
    'sam.rivera@example.com', 'P1 - High', 'Open', NOW() - INTERVAL '8 days'
  ),
  (
    'Audit log entries are missing the acting user for admin actions',
    'Admin actions performed through the impersonation flow record the '
    'impersonated user rather than the acting administrator. This is a '
    'compliance problem: the audit trail cannot establish who actually made a '
    'change. No data is lost, but the record is wrong.',
    'maya.okafor@example.com', 'P0 - Critical', 'In Progress', NOW() - INTERVAL '4 days'
  )
ON CONFLICT DO NOTHING;

-- Populate embeddings. Requires google_ml_integration (Cloud SQL / AlloyDB).
-- Skip this on local Postgres; `search-tickets` falls back to keyword search.
-- UPDATE tickets
--    SET embedding = embedding('text-embedding-005', title || ' ' || description)::vector
--  WHERE embedding IS NULL;
