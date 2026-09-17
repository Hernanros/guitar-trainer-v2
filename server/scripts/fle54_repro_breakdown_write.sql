-- FLE-54 follow-up: does a *realistic* breakdown write move the daily pick?
--
-- The first repro only set breakdown_generated_at. That column is not indexed and
-- the tuple is tiny, so Postgres took the HOT path: the heap tuple moved, the index
-- entry did not, and the index-ordered bank draw kept its winner. The real endpoint
-- writes `songs.breakdown` (a multi-KB JSONB) *and* the timestamp. Once the page has
-- no room for the new tuple version, the update is non-HOT: a new index entry is
-- created and index order changes with it.
--
-- Run: docker exec -i gt-postgres psql -U gt -d guitar_trainer < this_file

\set ON_ERROR_STOP on
\set UID '00000000-fe54-4fe5-8fe5-000000000002'

BEGIN;
INSERT INTO users (id, preferences) VALUES (:'UID', '{}'::jsonb) ON CONFLICT (id) DO NOTHING;
DELETE FROM songs WHERE user_id = :'UID';
INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category)
SELECT 'FLE54B Song ' || lpad(g::text, 2, '0'), 'FLE54B Artist ' || lpad(g::text, 2, '0'),
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
\echo '=== pick after each realistic breakdown write (same seed throughout) ==='

SELECT 'writes: 0' AS step, fle54_bank_pick(:'UID', '2026-09-17') AS pick;

-- One breakdown write = what GET /api/v1/songs/{id}/breakdown does on a cache miss:
-- replace the stub JSONB with a real multi-KB breakdown and stamp the timestamp.
DO $$
DECLARE
  target int;
  i int;
  body jsonb;
BEGIN
  body := jsonb_build_object(
    'tab', jsonb_build_object('measures', (SELECT jsonb_agg(jsonb_build_object(
              'index', m, 'notes', repeat('e|--3--5--7--|', 6)))
            FROM generate_series(1, 12) m),
            'tuning', '["E","A","D","G","B","e"]'::jsonb),
    'chords', '["Am","C","G","F"]'::jsonb,
    'technique_notes', to_jsonb(ARRAY[repeat('vibrato on the bends, keep the wrist loose. ', 20)]),
    'drills', '[]'::jsonb
  );
  FOR i IN 0..4 LOOP
    SELECT id INTO target FROM songs
      WHERE user_id = '00000000-fe54-4fe5-8fe5-000000000002'
      ORDER BY id OFFSET i LIMIT 1;
    UPDATE songs SET breakdown = body, breakdown_generated_at = now() WHERE id = target;
  END LOOP;
END $$;

SELECT 'writes: 5' AS step, fle54_bank_pick(:'UID', '2026-09-17') AS pick;

SELECT ctid, id, title, (breakdown_generated_at IS NOT NULL) AS has_breakdown
FROM songs WHERE user_id = :'UID' ORDER BY ctid;

\echo ''
\echo '=== index order the bank draw actually scans (songs_user_title_artist_uidx) ==='
EXPLAIN (COSTS OFF)
SELECT s.id::text FROM songs s WHERE s.user_id = :'UID' ORDER BY random() LIMIT 1;

DELETE FROM songs WHERE user_id = :'UID';
DELETE FROM users WHERE id = :'UID';
DROP FUNCTION fle54_bank_pick(text, text);
