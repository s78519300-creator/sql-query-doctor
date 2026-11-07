-- Week-over-week retention by signup cohort.
-- Written the way we want queries to look: explicit columns, partition-key
-- filters on the raw column, small table on the right of the join, and an
-- approximate distinct count.
WITH cohorts AS
(
    SELECT
        user_id,
        toMonday(min(event_date)) AS cohort_week
    FROM analytics.events
    PREWHERE event_date >= today() - 56
    WHERE event_type = 'signup'
    GROUP BY user_id
)
SELECT
    c.cohort_week,
    dateDiff('week', c.cohort_week, toMonday(e.event_date)) AS week_number,
    uniqCombined(e.user_id) AS active_users
FROM analytics.events AS e
INNER JOIN cohorts AS c ON c.user_id = e.user_id
WHERE e.event_date >= today() - 56
  AND e.event_type IN ('session_start', 'purchase')
GROUP BY c.cohort_week, week_number
ORDER BY c.cohort_week, week_number
LIMIT 1000;
