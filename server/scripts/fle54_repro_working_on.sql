-- FLE-54 follow-up 3: the 75% branch is not random at all, and it is the least
-- stable of the three.
--
-- working_on_pick is `ORDER BY sn.mastery ASC, sn.updated_at ASC LIMIT 1` over the
-- user's working_on songs. POST /api/v1/sessions raises skill_nodes.mastery on every
-- rating — a drill rating bumps one node, a whole-song rating bumps every leaf node.
-- So practising today's song changes argmin(mastery), and the next GET /song-of-day
-- the same day returns a different song. No seed, no plan, no heap involved.
--
-- Run: docker exec -i gt-postgres psql -U gt -d guitar_trainer < this_file

\set ON_ERROR_STOP on
\set UID '00000000-fe54-4fe5-8fe5-000000000004'

BEGIN;
INSERT INTO users (id, preferences) VALUES (:'UID', '{}'::jsonb) ON CONFLICT (id) DO NOTHING;
DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id = :'UID');
DELETE FROM songs WHERE user_id = :'UID';
DELETE FROM skill_nodes WHERE user_id = :'UID';

INSERT INTO songs (title, artist, genre, difficulty, breakdown, user_id, category)
SELECT 'FLE54D Song ' || g, 'FLE54D Artist ' || g, 'test', 'beginner',
       '{"tab":{"measures":[],"tuning":["E","A","D","G","B","e"]},"chords":[],"technique_notes":[]}'::jsonb,
       :'UID', 'working_on'
FROM generate_series(1, 3) g;

-- One leaf skill node per song, mastery 0.30 / 0.40 / 0.50.
INSERT INTO skill_nodes (id, user_id, name, level, mastery)
SELECT gen_random_uuid(), :'UID', 'FLE54D node ' || g, 'leaf', 0.20 + g * 0.10
FROM generate_series(1, 3) g;

INSERT INTO song_skills (song_id, skill_node_id)
SELECT s.id, n.id
FROM (SELECT id, row_number() OVER (ORDER BY id) rn FROM songs WHERE user_id = :'UID') s
JOIN (SELECT id, row_number() OVER (ORDER BY name) rn FROM skill_nodes WHERE user_id = :'UID') n
  ON n.rn = s.rn;
COMMIT;

CREATE OR REPLACE FUNCTION fle54_working_on_pick(p_user_id text)
RETURNS text LANGUAGE sql AS $fn$
  SELECT s.id::text
  FROM songs s
  JOIN song_skills ss ON ss.song_id = s.id
  JOIN skill_nodes sn ON sn.id = ss.skill_node_id
  WHERE s.user_id = CAST($1 AS uuid) AND s.category = 'working_on'
  ORDER BY sn.mastery ASC, sn.updated_at ASC
  LIMIT 1
$fn$;

\echo ''
\echo '=== 75% branch: practising today''s song hands the slot to another song ==='
SELECT 'morning' AS step,
       (SELECT title FROM songs WHERE id = fle54_working_on_pick(:'UID')::int) AS todays_song;

-- What POST /api/v1/sessions does on a "nailed it" rating: raise mastery on the
-- skill node(s) behind the song the user just practised.
UPDATE skill_nodes SET mastery = LEAST(mastery + 0.15, 1.0), updated_at = now()
WHERE user_id = :'UID'
  AND id IN (
    SELECT ss.skill_node_id FROM song_skills ss
    WHERE ss.song_id = fle54_working_on_pick(:'UID')::int
  );

SELECT 'after one rating' AS step,
       (SELECT title FROM songs WHERE id = fle54_working_on_pick(:'UID')::int) AS todays_song;

DELETE FROM song_skills WHERE song_id IN (SELECT id FROM songs WHERE user_id = :'UID');
DELETE FROM songs WHERE user_id = :'UID';
DELETE FROM skill_nodes WHERE user_id = :'UID';
DELETE FROM users WHERE id = :'UID';
DROP FUNCTION fle54_working_on_pick(text);
