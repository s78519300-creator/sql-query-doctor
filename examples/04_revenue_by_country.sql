-- Revenue per country for the finance dashboard.
SELECT DISTINCT
    c.name AS country,
    sum(o.amount) OVER (PARTITION BY c.name) AS revenue
FROM ref.countries AS c, analytics.users AS u
JOIN analytics.orders AS o ON o.user_id = u.user_id
WHERE u.country = c.code
  AND o.created_at >= '2026-01-01';

-- First-touch referrer for each new user: the big table is on the right.
SELECT u.user_id, e.referrer
FROM analytics.users AS u FINAL
LEFT JOIN analytics.events AS e ON e.user_id = u.user_id AND e.referrer = u.signup_referrer
WHERE u.signup_date >= today() - 7
  AND e.event_date >= today() - 7;
