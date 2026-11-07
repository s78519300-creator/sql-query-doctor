-- Cleanup jobs that were "temporarily" pasted into the ops runbook.

-- Remove a user's events (right-to-be-forgotten request).
ALTER TABLE analytics.events DELETE WHERE 1 = 1;

-- Reset the staging table before a backfill.
DELETE FROM staging.events_backfill;

-- Clear marketing consent for churned users.
UPDATE crm.contacts
SET marketing_opt_in = NULL;

-- Fine: scoped to one user and one partition range.
ALTER TABLE analytics.events
    DELETE WHERE user_id = {user_id:UInt64} AND event_date >= '2026-01-01';
