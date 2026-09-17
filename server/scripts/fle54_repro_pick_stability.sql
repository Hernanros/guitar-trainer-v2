-- FLE-54 investigation: is the seeded-random daily pick actually stable?
--
-- Claim under test (from the issue): setseed() makes the *sequence* of random()
-- values deterministic, not the *assignment* of values to rows. `ORDER BY random()
-- LIMIT 1` hands value #N to whatever row the scan produces Nth, so anything that
-- changes scan order can hand the win to a different song under the identical seed.
--
-- Run:  docker exec -i gt-postgres psql -U gt -d guitar_trainer < this_file
-- Safe: creates its own throwaway user, drops everything at the end.

\set ON_ERROR_STOP on
\set UID '00000000-fe54-4fe5-8fe5-000000000001'
\timing off

BEGIN;

-- ---------------------------------------------------------------------------
-- Fixture: one user, 15 aspirational songs, no working_on songs.
-- No working_on => D-04 forces the bank branch every time, which isolates the
-- `ORDER BY random() LIMIT 1` over `songs` that the issue points at.
-- ---------------------------------------------------------------------------
INSERT INTO users (id, preferences) VALUES (:'UID', '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

DELETE FROM songs WHERE user_id = :'UID';

INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category)
SELECT
  'FLE54 Song ' || lpad(g::text, 2, '0'),
  'FLE54 Artist ' || lpad(g::text, 2, '0'),
  'test',
  'beginner',
  '{"tab":{"measures":[],"tuning":["E","A","D","G","B","e"]},"chords":[],"technique_notes":[]}'::jsonb,
  :'UID',
  'aspirational'
FROM generate_series(1, 15) g;

COMMIT;

-- ---------------------------------------------------------------------------
-- The selector, verbatim from app/selectors/today_song.py (_SELECTOR_CTE),
-- wrapped in a function so we can call it repeatedly in one session.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fle54_pick(p_user_id text, p_day text, p_suffix text)
RETURNS text
LANGUAGE sql
AS $fn$
WITH
  seed AS (
    SELECT setseed(
      hashtext(CAST($1 AS text) || '|' || CAST($2 AS text) || CAST($3 AS text))
      / 2147483647.0
    )
  ),
  player_level_cte AS (
    SELECT GREATEST(COALESCE(AVG(mastery), 0.5), 0.20)::numeric(4,3) AS player_level
    FROM skill_nodes
    WHERE user_id = CAST($1 AS uuid)
      AND level = 'leaf'
  ),
  working_on_pick AS (
    SELECT s.id::text AS song_id
    FROM songs s
    JOIN song_skills ss ON ss.song_id = s.id
    JOIN skill_nodes sn ON sn.id = ss.skill_node_id
    WHERE s.user_id = CAST($1 AS uuid)
      AND s.category = 'working_on'
    ORDER BY sn.mastery ASC, sn.updated_at ASC
    LIMIT 1
  ),
  user_bench_pick AS (
    SELECT s.id::text AS song_id, 'user_bench'::text AS src
    FROM songs s
    WHERE s.user_id = CAST($1 AS uuid)
    ORDER BY random()
    LIMIT 1
  ),
  catalog_pick AS (
    SELECT sc.id::text AS song_id, 'seed_catalog'::text AS src
    FROM song_catalog sc, player_level_cte pl
    WHERE ABS(sc.difficulty - pl.player_level) <= 0.15
    ORDER BY random()
    LIMIT 1
  ),
  bank_pick AS (
    SELECT song_id, src FROM user_bench_pick
    UNION ALL
    SELECT song_id, src FROM catalog_pick
    WHERE NOT EXISTS (SELECT 1 FROM user_bench_pick)
    LIMIT 1
  ),
  coin AS (
    SELECT random() AS flip
  )
SELECT
  CASE
    WHEN (SELECT flip FROM coin) < 0.25 THEN (SELECT song_id FROM bank_pick)
    WHEN NOT EXISTS (SELECT 1 FROM working_on_pick) THEN (SELECT song_id FROM bank_pick)
    ELSE (SELECT song_id FROM working_on_pick)
  END AS today_song_id
FROM seed
$fn$;

\echo ''
\echo '=== 0. which plan does the bank draw get? ==='
EXPLAIN (COSTS OFF)
SELECT s.id::text FROM songs s WHERE s.user_id = :'UID' ORDER BY random() LIMIT 1;

-- ---------------------------------------------------------------------------
-- A. control: two identical calls, nothing mutated.
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== A. same seed, no mutation (control) ==='
SELECT 'call 1' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick
UNION ALL SELECT 'call 2', fle54_pick(:'UID', '2026-09-17', '');

-- ---------------------------------------------------------------------------
-- B. the real-world mutation: breakdown writes on other songs of the same user.
--    (`breakdown_generated_at` is not indexed, so these are HOT updates when the
--    page has room: the heap tuple moves, the index entry does not.)
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== B. same seed, breakdown writes in between (default plan) ==='
SELECT 'before' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;

UPDATE songs SET breakdown_generated_at = now()
WHERE id = (SELECT id FROM songs WHERE user_id = :'UID' ORDER BY id LIMIT 1);
SELECT 'after 1 breakdown write' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;

UPDATE songs SET breakdown_generated_at = now()
WHERE id = (SELECT id FROM songs WHERE user_id = :'UID' ORDER BY id OFFSET 1 LIMIT 1);
SELECT 'after 2 breakdown writes' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;

UPDATE songs SET breakdown_generated_at = now()
WHERE id = (SELECT id FROM songs WHERE user_id = :'UID' ORDER BY id OFFSET 2 LIMIT 1);
SELECT 'after 3 breakdown writes' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;

\echo ''
\echo '=== B2. physical (ctid) order vs index order after those writes ==='
SELECT ctid, id, title FROM songs WHERE user_id = :'UID' ORDER BY ctid;

-- ---------------------------------------------------------------------------
-- C. same seed, same data, only the PLAN changes. If the pick moves here, the
--    day's song is a function of the planner, not of the seed.
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== C. same seed, plan forced to seq scan ==='
SET enable_indexscan = off;
SET enable_indexonlyscan = off;
SET enable_bitmapscan = off;
EXPLAIN (COSTS OFF)
SELECT s.id::text FROM songs s WHERE s.user_id = :'UID' ORDER BY random() LIMIT 1;
SELECT 'seqscan plan' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;
RESET enable_indexscan;
RESET enable_indexonlyscan;
RESET enable_bitmapscan;
SELECT 'index plan' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;

-- ---------------------------------------------------------------------------
-- D. same seed, index plan, but a NON-HOT update (touches an indexed column):
--    the index entry is rewritten, so index order changes too.
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== D. same seed, one indexed-column write (non-HOT) in between ==='
SELECT 'before' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;
UPDATE songs SET title = 'FLE54 Song zz'
WHERE id = (SELECT id FROM songs WHERE user_id = :'UID' ORDER BY id LIMIT 1);
SELECT 'after retitle' AS call, fle54_pick(:'UID', '2026-09-17', '') AS pick;

-- ---------------------------------------------------------------------------
-- E. the bare mechanism: value #N goes to scan row #N, under one fixed seed.
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== E. seeded random() values land in scan order, not on identities ==='
SELECT setseed(0.42);
SELECT id, title, random() AS drew FROM songs WHERE user_id = :'UID' ORDER BY id LIMIT 5;
SELECT setseed(0.42);
SELECT id, title, random() AS drew FROM songs WHERE user_id = :'UID' ORDER BY id DESC LIMIT 5;

-- ---------------------------------------------------------------------------
-- Cleanup
-- ---------------------------------------------------------------------------
DELETE FROM songs WHERE user_id = :'UID';
DELETE FROM users WHERE id = :'UID';
DROP FUNCTION fle54_pick(text, text, text);
