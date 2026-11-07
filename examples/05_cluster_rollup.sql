-- Cross-shard rollup run from the BI tool against the Distributed tables.
SELECT
    e.country_code,
    count() AS events,
    uniq(e.session_id) AS sessions
FROM analytics.events_all AS e
WHERE e.event_date >= today() - 7
  AND e.user_id IN (
      SELECT user_id
      FROM analytics.users_all
      WHERE plan = 'enterprise' AND churned_at = NULL
  )
GROUP BY e.country_code;

-- Paginated export endpoint; page 500 of 100 rows.
SELECT view_id, viewed_at, url
FROM analytics.page_views
ORDER BY viewed_at DESC
LIMIT 100 OFFSET 50000;
