-- Daily active users for yesterday, split by platform.
-- Looks innocent, but toDate() hides the partition column from the pruner
-- and uniqExact keeps every user_id in memory.
SELECT
    platform,
    uniqExact(user_id) AS dau
FROM analytics.events
WHERE toDate(event_time) = yesterday()
  AND event_type = 'session_start'
GROUP BY platform;

-- Same thing, rewritten: range on the raw columns, approximate distinct.
SELECT
    platform,
    uniqCombined(user_id) AS dau
FROM analytics.events
WHERE event_date = yesterday()
  AND event_type = 'session_start'
GROUP BY platform
ORDER BY dau DESC;
