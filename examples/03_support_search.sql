/*
 * Support tooling: find sessions where the user hit an error page.
 * Note the LIKE inside this comment ('%ignored%') is not reported.
 */
SELECT *
FROM analytics.events
WHERE event_date >= today() - 30
  AND page_url LIKE '%/checkout/error%'
  AND (event_type = 'page_view' OR event_type = 'error' OR event_type = 'js_exception')
  AND user_id NOT IN (SELECT user_id FROM analytics.internal_users)
ORDER BY event_time DESC;

-- Strings are opaque to the linter: this message text mentions SELECT * and
-- '= NULL' but nothing in it is flagged.
SELECT event_id, 'SELECT * FROM x WHERE y = NULL' AS sample_message
FROM analytics.events
WHERE event_date = today()
LIMIT 10;
