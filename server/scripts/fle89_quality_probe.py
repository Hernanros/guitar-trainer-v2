"""FLE-89 Phase A probe — read-only, zero spend, no LLM.

Measures Hernan's three 2026-10-04 complaints against breakdowns already cached in
prod. No model calls, no writes, no paid breakdown — safe to run as often as you like.

    PGURL=$(railway variables --service Postgres --json \
            | python3 -c "import json,sys;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])") \
      server/.venv/bin/python server/scripts/fle89_quality_probe.py

It answers three questions the plan was guessing at:

 1. TUNING. Does the model-chosen tuning (breakdown.py:143-187) agree with the
    ground truth in song_catalog.tuning? The catalog stores an enum ('eb_standard');
    the breakdown stores a note list. They must be normalised before comparing, or
    every row reads as a mismatch when it is only a format difference. First run
    (2026-10-04): 4 of 4 AGREE. Tuning is not the cause of the wrong tabs.
    Re-run 2026-10-05 (FLE-102): corpus is now 10 cached tabs and the catalog has
    tuning on all 64 rows, so 8 of the 10 join -- 8 of 8 AGREE. Confirmed harder.
    CAVEAT this n hides: the other 2 ("Cause We've Ended as Lovers" by Jeff Beck,
    "Big Love" credited to Fleetwood Mac rather than the catalog's Lindsey
    Buckingham) have no catalog row, so their tuning is never checked at all.
    A 100% agreement rate here only covers the songs that joined.

 2. DRILL -> SONG LINK. Does anything record which passage a drill prepares?
    First run: 0 columns, 0/6 breakdowns with section labels. Confirmed missing.

 3. BLANDNESS. The prompt's anti-slice rule (breakdown.py:210-227) forbids a drill
    snippet from sharing 3+ consecutive BEATS with the song tab. longest_common_run
    reimplements that exact comparison so the measurement and the rule cannot drift.
    First run: 8 of 16 drills violate it. The rule is not binding, so relaxing it
    would not have fixed the blandness — which is in the drill copy, not the notes.

Keep the first-run numbers above updated when the corpus grows; the whole point of
this script is that the claim and the number stay attached to each other.

What this script does NOT measure: whether the notes are right. See
server/scripts/fle102_tab_audit.py and
.planning/investigations/261005-fle102-tab-error-taxonomy.md — 0 of 10 cached tabs
are a correct rendering of the song they name, and the machine-checkable checks
pass 62% of bars, so they cannot be used as the accuracy gate on their own.
"""
import os, json, psycopg2
from collections import Counter

cur = psycopg2.connect(os.environ["PGURL"]).cursor()

# --- 0. corpus size -------------------------------------------------------
cur.execute("select count(*) from songs")
total = cur.fetchone()[0]
cur.execute("select count(*) from songs where breakdown is not null and breakdown->'tab'->'measures' is not null and jsonb_array_length(breakdown->'tab'->'measures') > 0")
cached = cur.fetchone()[0]
cur.execute("select count(*), count(tuning) from song_catalog")
cat_total, cat_tuned = cur.fetchone()
print(f"songs rows={total}  with non-empty cached tab={cached}")
print(f"song_catalog rows={cat_total}  with tuning set={cat_tuned}")

# --- 1. TUNING: model-chosen vs catalog ground truth ----------------------
cur.execute("""
  select s.title, s.artist, c.tuning, s.breakdown->'tab'->'tuning'
  from songs s join song_catalog c
    on lower(s.title)=lower(c.title) and lower(s.artist)=lower(c.artist)
  where s.breakdown->'tab'->'measures' is not null
    and jsonb_array_length(s.breakdown->'tab'->'measures') > 0
    and c.tuning is not null
""")
rows = cur.fetchall()
def norm(t):
    if t is None: return None
    if isinstance(t, str):
        t = t.replace("-", " ").replace(",", " ").split()
    return [x.strip().lower() for x in t]
match = mismatch = 0
examples = []
for title, artist, cat_t, bd_t in rows:
    a, b = norm(cat_t), norm(bd_t)
    if a == b: match += 1
    else:
        mismatch += 1
        if len(examples) < 8: examples.append((title, artist, cat_t, bd_t))
print(f"\n--- TUNING (joinable + catalog tuning set): n={len(rows)}")
print(f"    agrees with catalog: {match}   DISAGREES: {mismatch}")
for e in examples: print(f"    MISMATCH  {e[0]} — {e[1]}: catalog={e[2]!r} breakdown={e[3]!r}")

# what tunings does the model emit at all?
cur.execute("""select breakdown->'tab'->'tuning', count(*) from songs
  where breakdown->'tab'->'measures' is not null group by 1 order by 2 desc limit 10""")
print("    emitted tunings:", [(json.dumps(t), n) for t, n in cur.fetchall()])

# --- 2. DRILL -> SONG LINK ------------------------------------------------
cur.execute("""select count(*) from information_schema.columns
  where table_name='drills' and column_name in ('prepares_section','section_id','passage','start_beat','end_beat')""")
print(f"\n--- DRILL->SONG LINK")
print(f"    drills columns naming a passage: {cur.fetchone()[0]}")
cur.execute("""select count(*) from songs where breakdown ? 'sections'
   or breakdown->'tab' ? 'sections'""")
print(f"    cached breakdowns carrying section labels: {cur.fetchone()[0]} / {cached}")
cur.execute("select count(*) from drills")
print(f"    banked drills: {cur.fetchone()[0]}")

# --- 3. BLANDNESS: does a drill share any musical material with its song? -
def beats_of(tab):
    """ordered list of beat signatures (frozenset of string/fret)"""
    out = []
    for m in (tab or {}).get("measures", []):
        for bt in m.get("beats", []):
            out.append(tuple(sorted((n.get("string"), n.get("fret")) for n in bt.get("notes", []))))
    return out

def longest_common_run(a, b):
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i+k < len(a) and j+k < len(b) and a[i+k] == b[j+k]: k += 1
            best = max(best, k)
    return best

cur.execute("""select s.title, s.breakdown->'tab', s.breakdown->'drills'
  from songs s where s.breakdown->'drills' is not null
    and jsonb_array_length(s.breakdown->'drills') > 0
    and jsonb_array_length(s.breakdown->'tab'->'measures') > 0""")
runs = Counter(); ndrills = 0; nsongs = 0; percount = Counter()
zero_overlap_songs = 0
for title, tab, drills in cur.fetchall():
    nsongs += 1
    song_beats = beats_of(tab)
    percount[len(drills)] += 1
    song_max = 0
    for d in drills:
        ndrills += 1
        r = longest_common_run(beats_of(d.get("tab_snippet")), song_beats)
        runs[min(r, 3)] += 1
        song_max = max(song_max, r)
    if song_max == 0: zero_overlap_songs += 1
print(f"\n--- DRILL<->SONG MUSICAL OVERLAP  (songs={nsongs}, drills={ndrills})")
print(f"    drills per breakdown: {dict(sorted(percount.items()))}")
print(f"    longest run of beats a drill shares with its song tab:")
for k in sorted(runs): print(f"      {'3+' if k==3 else k} beat(s): {runs[k]} drills ({100*runs[k]/max(ndrills,1):.0f}%)")
print(f"    breakdowns where NO drill shares even one beat with the song: {zero_overlap_songs}/{nsongs}")
