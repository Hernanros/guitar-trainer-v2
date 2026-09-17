-- FLE-54 follow-up 2: the bank draw scans songs_user_title_artist_uidx, so the thing
-- that reorders it is not a heap move — it is any change to the *index key set* for
-- that user. Adding a song shifts every later row's position in the index, and the
-- seeded random sequence is handed out by position.
--
-- Two triggers tested here, both of which happen during normal use:
--   1. The user adds a song (Settings -> add).
--   2. The selector itself adds one: _ensure_catalog_song_as_user_song UPSERTs a
--      catalog pick into `songs` on the seed_catalog branch.
--
-- Run: docker exec -i gt-postgres psql -U gt -d guitar_trainer < this_file

\set ON_ERROR_STOP on
\set UID '00000000-fe54-4fe5-8fe5-000000000003'

BEGIN;
INSERT INTO users (id, preferences) VALUES (:'UID', '{}'::jsonb) ON CONFLICT (id) DO NOTHING;
DELETE FROM songs WHERE user_id = :'UID';
INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category)
SELECT 'FLE54C Song ' || lpad(g::text, 2, '0'), 'FLE54C Artist ' || lpad(g::text, 2, '0'),
       'test', 'beginner',
       '{"tab":{"measures":[],"tuning":["E","A","D","G","B","e"]},"chords":[],"technique_notes":[]}'::jsonb,
       :'UID', 'aspirational'
FROM generate_series(1, 15) g;
COMMIT;

CREATE OR REPLACE FUNCTION fle54_bank_pick(p_user_id text, p_day text)
RETURNS text LANGUAGE sql AS $fn$
WITH seed AS (
  SELECT setseed(hashtext(CAST($1 AS text) || '|' || CAST($2 AS text) || CAST('' AS text)) / 2147483647.0)
),
user_bench_pick AS (
  SELECT s.id::text AS song_id FROM songs s WHERE s.user_id = CAST($1 AS uuid)
  ORDER BY random() LIMIT 1
)
SELECT (SELECT song_id FROM user_bench_pick) FROM seed
$fn$;

\echo ''
\echo '=== adding one song to the bank, same seed, same day ==='
SELECT 'before add' AS step,
       fle54_bank_pick(:'UID', '2026-09-17') AS pick,
       (SELECT title FROM songs WHERE id = fle54_bank_pick(:'UID', '2026-09-17')::int) AS title;

-- A song whose title sorts FIRST, so every existing row shifts one position later.
INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category)
VALUES ('AAA Newly Added', 'FLE54C Artist 00', 'test', 'beginner',
        '{"tab":{"measures":[],"tuning":["E","A","D","G","B","e"]},"chords":[],"technique_notes":[]}'::jsonb,
        :'UID', 'aspirational');

SELECT 'after add' AS step,
       fle54_bank_pick(:'UID', '2026-09-17') AS pick,
       (SELECT title FROM songs WHERE id = fle54_bank_pick(:'UID', '2026-09-17')::int) AS title;

DELETE FROM songs WHERE user_id = :'UID';
DELETE FROM users WHERE id = :'UID';
DROP FUNCTION fle54_bank_pick(text, text);
