# server/app/sessions/family_classify.py
#
# §8 family classification for banked drills — the one column of FLE-13 that is not
# derivable from data `drills` already has.
#
# Pure functions. No DB, no I/O, no LLM — same contract as taxonomy.py, and for the
# same reason: this runs over the whole bank in a backfill and on every insert in the
# write path, and FLE-8's standing constraint is that no Sonnet call sits in either.
#
# THE RULE, AND WHY IT IS SHAPED THIS WAY
# ---------------------------------------
# The skill node's ROOT narrows the 18 families to the 2-4 that hang off it (§8's
# grid is rooted), then keyword markers pick the mechanic inside that set. Root
# first is not an optimisation — it is what keeps the classifier honest. "Palm mute"
# appearing in a drill whose node roots to chord_voicings is far more likely to be a
# passing mention in prose than a misfiled R2 drill, and R2 is not reachable from
# chord_voicings anyway.
#
# WHEN IN DOUBT, RETURN NOTHING. An unmatched or ambiguous drill classifies to None
# and stays NULL in the column. That is deliberate and it is the whole safety
# property of this module: §10.1 exists to stop the bank reporting coverage it does
# not have, so a guessed family is strictly worse than no family. A NULL undercounts
# a cell; a wrong value fabricates one. Undercounting is recoverable — the drill gets
# classified later, by rule or by a one-off Sonnet pass over exactly the rows
# `unclassified()` returns. Fabricated coverage is not: it passes the §10.3 gate with
# a hole behind it.
#
# Spec section references are to the `Session Structure & Drill Taxonomy` document on
# FLE-4. Changing a marker or a threshold here changes which cells the bank reports
# as stocked, so it is a CLASSIFIER_VERSION bump — stored on the backfill's report so
# a coverage number can always be traced to the rules that produced it.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from app.sessions.taxonomy import FAMILY_SPECS, SkillRoot, TechniqueFamily

__all__ = [
    "CLASSIFIER_VERSION",
    "FamilyVerdict",
    "classify_family",
    "families_for_root",
    "MARKERS",
]

# Bumped whenever a marker, weight or threshold below changes. Recorded alongside
# every backfilled family so a coverage report names the rules that produced it.
CLASSIFIER_VERSION = 2

# A marker must clear this to classify at all. Every family below has exactly ONE
# primary row and ONE secondary row, so this threshold says precisely one thing: a
# family needs a PRIMARY hit. Secondary-only evidence caps at NAME_WEIGHT * SECONDARY
# = 2 and can never reach it.
#
# That is deliberate and FLE-80 re-confirmed it with numbers rather than softening it.
# All eight drills FLE-80 measured as declined scored 1 or 2 — secondary-only — which
# reads like a threshold set one point too high until you look at what the 2s were
# made of: "Full Root-Fifth-Treble Pattern on a Held Shape" scores 2 on R1 from the
# single word "pattern". Dropping MIN_SCORE to 2 would file that fingerpicking drill
# as a strumming-pattern drill. The weights and the threshold agree; what was missing
# was primary vocabulary, and that is what FLE-80 added.
MIN_SCORE = 3
# The winner must beat the runner-up by this much. A drill that reads equally as two
# families is genuinely ambiguous and returns None rather than picking the lower enum.
MIN_MARGIN = 2
# The drill's NAME is its headline mechanic; its prose mentions everything it touches.
# Markers found in the name count double.
NAME_WEIGHT = 2

PRIMARY = 3
SECONDARY = 1


def _rx(*alternatives: str) -> re.Pattern[str]:
    """Word-boundary alternation. Markers are phrases, so \\b on both ends."""
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b")


# ---------------------------------------------------------------------------
# §8 markers, one table per family.
#
# PRIMARY markers name the mechanic the family IS. SECONDARY markers are supporting
# vocabulary that cannot carry a classification alone but breaks ties between two
# families that both scored a primary.
#
# Hyphens are normalised to spaces before matching (see _normalize), so "palm-muted"
# and "palm muted" are one marker, and "thumb-over" is written "thumb over". The flip
# side bit FLE-80: "downstroke" and "down-stroke" are NOT one marker, because the
# second normalises to "down stroke". Both spellings have to be listed.
#
# HOW THE THREE RHYTHM FAMILIES DIVIDE, because FLE-80 found the R tables were the
# thin ones and "shuffle" could plausibly have gone in any of them:
#   R1 is the STROKE      — what the pick hand does across the strings.
#   R2 is the ARTICULATION — how long and how loud each attack is allowed to be.
#   R3 is the FIGURE      — a repeating rhythmic shape that has to lock to the pulse.
# R3 is therefore the residual rhythm family, and that is not a dumping ground: under
# the rooted grid, a mechanic that would read as F1 or M2 from its own root reads as
# "a figure locked to the pulse" once the skill graph has filed it under rhythm.
# ---------------------------------------------------------------------------

MARKERS: dict[TechniqueFamily, tuple[tuple[int, re.Pattern[str]], ...]] = {
    # --- rhythm ---
    TechniqueFamily.R1: (  # Strumming patterns
        # "down stroke"/"up stroke" are the hyphenated spellings after _normalize;
        # "brush" is the word drill prose actually uses for an upward finger sweep.
        (PRIMARY, _rx("strum", "strums", "strumming", "strummed", "downstroke",
                      "downstrokes", "upstroke", "upstrokes", "down stroke",
                      "down strokes", "up stroke", "up strokes", "strum pattern",
                      "strumming pattern", "brush", "brushing", "brushed")),
        (SECONDARY, _rx("pick depth", "wrist rotation", "stroke", "pattern", "sweep",
                        "pick hand", "rake")),
    ),
    TechniqueFamily.R2: (  # Palm muting & dynamics
        (PRIMARY, _rx("palm mute", "palm muted", "palm muting", "palm mutes", "muting",
                      "muted strum", "muted strums", "staccato", "string damping",
                      "damping", "damped", "choke", "chokes", "dig in",
                      "dynamic control", "accent pattern")),
        (SECONDARY, _rx("mute", "muted", "mutes", "bridge", "dynamics", "accent",
                        "accents", "volume", "let ring", "ring out")),
    ),
    TechniqueFamily.R3: (  # Riff rhythm lock-up
        # The FIGURE family. FLE-80's eight declines were almost all here: the drill
        # prompt names a blues rhythm figure by its own vocabulary — shuffle, pulse,
        # boogie, alternating bass, chord stab, double-stop lick — and none of that
        # was primary, so seven rhythm-rooted drills in a row scored 0-2.
        #
        # "shuffle" is deliberately a SECONDARY while "shuffle pulse"/"shuffle grip"/
        # "shuffle feel" are primary. Bare "shuffle" as a primary would tie 6-6 with
        # R1 on a node called "Blues Shuffle Strumming" and then win on headline
        # ORDER, filing a strumming drill as a figure drill. Specificity is the right
        # tie-break here, not position.
        (PRIMARY, _rx("riff", "riffs", "power chord", "power chords",
                      "shuffle pulse", "shuffle grip", "shuffle feel", "shuffle rhythm",
                      "shuffle figure", "shuffle groove", "swung eighth", "swung eighths",
                      "boogie", "pulse", "alternating bass", "bass alternation",
                      "bass pulse", "bass figure", "double stop", "double stops",
                      "chord stab", "chord stabs", "chord chop", "chord chops",
                      "rhythm figure", "rhythm lick", "rhythm licks", "comping",
                      "vamp", "vamps", "groove lock", "lock up the groove")),
        (SECONDARY, _rx("lock", "locked", "lock up", "groove", "tight", "unison",
                        "shuffle", "swing", "swung", "feel", "continuous", "unbroken")),
    ),
    # --- lead ---
    TechniqueFamily.L1: (  # Alternate picking & speed
        (PRIMARY, _rx("alternate picking", "alternate pick", "tremolo", "picking speed",
                      "economy picking")),
        (SECONDARY, _rx("speed", "down up", "pick stroke", "pick strokes", "fast")),
    ),
    TechniqueFamily.L2: (  # Bending & vibrato
        (PRIMARY, _rx("bend", "bends", "bending", "vibrato", "pre bend", "unison bend")),
        (SECONDARY, _rx("pitch", "in tune", "release", "semitone", "whole step")),
    ),
    TechniqueFamily.L3: (  # Legato (slides, hammers, pulls)
        (PRIMARY, _rx("legato", "slide", "slides", "sliding", "hammer on", "hammer ons",
                      "pull off", "pull offs", "hammer", "trill")),
        (SECONDARY, _rx("fretting hand pressure", "no second pick", "smooth", "ornament")),
    ),
    TechniqueFamily.L4: (  # Scale shapes & position shifts
        (PRIMARY, _rx("scale", "scales", "position shift", "position shifts", "pentatonic",
                      "mode", "modes", "box shape", "shift position")),
        (SECONDARY, _rx("position", "shift", "shifts", "ascending", "descending", "run")),
    ),
    # --- chord voicings ---
    TechniqueFamily.C1: (  # Open chords & transitions
        # "open E shape", "open E chord shape", "partial open E voicing" — how drill
        # prose actually names an open chord. FLE-80's one Chord-Voicings decline said
        # "Hold an open E shape" and matched nothing, because "open chord" and "open
        # voicing" both miss when the chord's letter sits in the middle of the phrase.
        (PRIMARY, _rx("open chord", "open chords", "chord change", "chord changes",
                      "chord transition", "chord transitions", "cold switch", "cold change",
                      "open position", "open voicing", "open voicings",
                      "open shapes?", "open chord shapes?",
                      "open [a-g] (?:chord )?shapes?",
                      "open [a-g] (?:chord )?voicings?")),
        # Suspension vocabulary lives here rather than under C4: a sus2/sus4 is a
        # SUSPENSION, not an extension, and the drills that use one are open-position
        # drone and shape-holding drills. Filing them under "Extended & jazz voicings"
        # would put beginner material in a D4-D5-only family.
        (SECONDARY, _rx("open", "change", "changes", "switch", "transition", "pivot",
                        "anchor", "sus2", "sus4", "suspension", "suspended", "drone")),
    ),
    TechniqueFamily.C2: (  # Barre chords
        (PRIMARY, _rx("barre", "barres", "bar chord", "bar chords", "e shape barre",
                      "a shape barre", "flat barre")),
        (SECONDARY, _rx("index finger", "full six string", "clamp", "e shape", "a shape")),
    ),
    TechniqueFamily.C3: (  # Triads & inversions
        (PRIMARY, _rx("triad", "triads", "inversion", "inversions", "first inversion",
                      "second inversion", "close voicing")),
        (SECONDARY, _rx("three string", "upper strings", "string set", "voice leading")),
    ),
    TechniqueFamily.C4: (  # Extended & jazz voicings
        (PRIMARY, _rx("extended voicing", "extended voicings", "jazz voicing",
                      "jazz voicings", "drop 2", "drop 3", "shell voicing",
                      "9th", "11th", "13th", "maj7", "m7b5", "altered")),
        (SECONDARY, _rx("extension", "extensions", "colour", "color", "tension", "jazz")),
    ),
    # --- fingerstyle ---
    TechniqueFamily.F1: (  # Thumb independence & bass lines
        (PRIMARY, _rx("thumb", "thumbs", "thumb over", "bass line", "bass lines",
                      "bass note", "bass notes", "alternating bass")),
        (SECONDARY, _rx("independence", "bass", "low string", "anchor", "planted")),
    ),
    TechniqueFamily.F2: (  # Arpeggio patterns
        (PRIMARY, _rx("arpeggio", "arpeggios", "arpeggiate", "arpeggiated", "finger roll",
                      "rolling", "roll")),
        (SECONDARY, _rx("one note at a time", "cascade", "let each ring", "sustain",
                        "index middle ring", "pluck")),
    ),
    TechniqueFamily.F3: (  # Hybrid & Travis picking
        (PRIMARY, _rx("travis", "travis picking", "hybrid picking", "hybrid pick",
                      "pick and fingers", "claw")),
        (SECONDARY, _rx("pima", "pick hand fingers", "syncopated bass")),
    ),
    # --- music theory ---
    TechniqueFamily.T1: (  # Fretboard mapping
        (PRIMARY, _rx("fretboard", "fret board", "note names", "octave shape",
                      "octave shapes", "fretboard map", "root note location")),
        (SECONDARY, _rx("name the note", "locate", "map", "navigate the neck")),
    ),
    TechniqueFamily.T2: (  # Intervals & chord construction
        (PRIMARY, _rx("interval", "intervals", "chord construction", "chord tone",
                      "chord tones", "harmonise", "harmonize", "spell the chord")),
        (SECONDARY, _rx("third", "fifth", "seventh", "root", "degree", "degrees")),
    ),
    # --- timing ---
    TechniqueFamily.M1: (  # Subdivision & click discipline
        (PRIMARY, _rx("subdivision", "subdivisions", "subdivide", "click", "clicks",
                      "metronome", "count aloud")),
        (SECONDARY, _rx("even", "steady", "per beat", "on the beat", "timing")),
    ),
    TechniqueFamily.M2: (  # Syncopation & feel
        (PRIMARY, _rx("syncopation", "syncopated", "cross rhythm", "cross rhythms",
                      "polyrhythm", "3 against 4", "three against four", "swing",
                      "shuffle", "off beat", "offbeat", "ghost note", "ghost notes")),
        (SECONDARY, _rx("accent", "accents", "grouping", "feel", "push", "and of")),
    ),
}


def families_for_root(root: Optional[SkillRoot]) -> tuple[TechniqueFamily, ...]:
    """The families reachable from a root, in enum order.

    `root=None` — the drill's node has no root ancestor, which snapshot.py already
    treats as a normal state rather than an error — widens the search to all 18.
    That is the more permissive branch, and the ambiguity guard below is what keeps
    it from becoming the sloppier one.
    """
    if root is None:
        return tuple(TechniqueFamily)
    return tuple(f for f in TechniqueFamily if FAMILY_SPECS[f].root is root)


@dataclass(frozen=True)
class FamilyVerdict:
    """A classification and the evidence for it.

    `family=None` means unclassified, and `reason` says which of the two ways it got
    there — `no_marker` (nothing scored) or `ambiguous` (two families tied inside the
    margin and the headline-order tie-break could not separate them). The backfill
    reports those counts separately because they need different remedies: no_marker
    wants a new marker, ambiguous wants a human or a Sonnet pass.

    `reason == "matched_by_position"` is a match that only survived because of the
    headline-order tie-break, and it is reported separately for exactly that reason —
    those are the rows to spot-check first if a coverage number looks too good.

    `cross_root_hint` names a family OUTSIDE the root's set that outscored everything
    inside it. It never changes the verdict — root-narrowing is the rule, and this
    module does not get to overrule the skill graph. It is recorded because a drill
    whose text screams "syncopation" while its node roots to `rhythm` is evidence
    about the SKILL GRAPH, not about the drill: either the node is rooted wrong or
    §8's grid needs the family reachable from a second root. The backfill tallies
    these so that argument can be had with numbers.
    """

    family: Optional[TechniqueFamily]
    score: int
    runner_up: Optional[TechniqueFamily]
    runner_up_score: int
    reason: str
    cross_root_hint: Optional[TechniqueFamily] = None
    cross_root_score: int = 0

    @property
    def classified(self) -> bool:
        return self.family is not None


def _normalize(text: str) -> str:
    """Lowercase, hyphens and slashes to spaces, collapse whitespace.

    Hyphen folding is what lets one marker cover "palm-muted", "palm muted" and
    "Palm-Mute" — real drill names use all three.
    """
    lowered = text.lower()
    lowered = re.sub(r"[-/_]+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _score(family: TechniqueFamily, name: str, body: str) -> int:
    """Sum of matched marker weights, name matches counting NAME_WEIGHT times.

    Each (weight, pattern) ROW scores at most once, and a row is an alternation of
    synonyms — so adding "palm mutes" beside "palm mute" widens what is recognised
    without inflating the score of text that says both. Weight is carried by how many
    DISTINCT rows hit, and since every family has exactly one primary row and one
    secondary row, the arithmetic reduces to: a family cannot clear MIN_SCORE without
    a PRIMARY hit. Secondary-only tops out at NAME_WEIGHT * SECONDARY = 2. See the
    MIN_SCORE comment for why FLE-80 kept it that way instead of lowering the bar.
    """
    total = 0
    for weight, pattern in MARKERS[family]:
        if pattern.search(name):
            total += weight * NAME_WEIGHT
        elif pattern.search(body):
            total += weight
    return total


def _first_marker_offset(family: TechniqueFamily, headline: str) -> Optional[int]:
    """Where this family's earliest marker starts in the headline. None if absent.

    The tie-break axis. Drill names lead with the mechanic and trail with the vehicle
    — "Thumb-Bass then Finger Roll: Two-String Arpeggio" is a thumb-independence drill
    that happens to use an arpeggio, and it says so by putting the thumb first.
    """
    offsets = [m.start() for _, p in MARKERS[family] if (m := p.search(headline))]
    return min(offsets) if offsets else None


def classify_family(
    *,
    root: Optional[SkillRoot],
    name: str,
    skill_node_name: str = "",
    what: str = "",
    success_criterion: str = "",
    common_trap: str = "",
    allowed: Optional[Iterable[TechniqueFamily]] = None,
) -> FamilyVerdict:
    """§8 — classify one drill into a technique family, or decline to.

    Args:
        root: the drill's skill-node root, which narrows the candidate set. None
            widens to all 18 rather than failing.
        name: the drill's name. Markers here count NAME_WEIGHT times — a drill called
            "Single-String Palm Mute Lock" is an R2 drill; one that merely mentions
            palm muting in its trap text is not.
        skill_node_name: the target skill's name, scored with the name field. It is
            the other place the mechanic is stated outright ("palm-muted eighth
            notes"), and it is stated by the skill graph rather than by the drill
            prompt, so it is the more trustworthy of the two.
        allowed: override the root-derived candidate set. Exists for the backfill's
            `--only-family` re-runs and for tests; production passes None.

    Returns:
        A FamilyVerdict. Callers that only want the column write `verdict.family`,
        which is None for both unclassified reasons.
    """
    candidates = tuple(allowed) if allowed is not None else families_for_root(root)
    if not candidates:
        return FamilyVerdict(None, 0, None, 0, "no_candidates")

    headline = _normalize(f"{name} {skill_node_name}")
    body = _normalize(f"{what} {success_criterion} {common_trap}")

    scored = sorted(
        ((f, _score(f, headline, body)) for f in candidates),
        key=lambda pair: (-pair[1], pair[0].value),
    )
    best, best_score = scored[0]
    runner, runner_score = scored[1] if len(scored) > 1 else (None, 0)

    # Advisory only — see FamilyVerdict.cross_root_hint. Skipped when the caller
    # already widened the search, because then there is no "outside" to report.
    hint, hint_score = None, 0
    if allowed is None and root is not None:
        outside = [(f, _score(f, headline, body)) for f in TechniqueFamily
                   if f not in candidates]
        if outside:
            cand, cand_score = max(outside, key=lambda pair: (pair[1], -_FAMILY_ORDER[pair[0]]))
            if cand_score >= MIN_SCORE and cand_score > best_score:
                hint, hint_score = cand, cand_score

    if best_score < MIN_SCORE:
        return FamilyVerdict(None, best_score, runner, runner_score, "no_marker",
                             hint, hint_score)

    if best_score - runner_score < MIN_MARGIN:
        # Headline order breaks the tie, but only when it is unambiguous itself:
        # both families must actually appear in the headline and at different
        # offsets. A tie decided entirely in the body text has no leading mechanic
        # to read, so it stays ambiguous.
        best_at = _first_marker_offset(best, headline)
        runner_at = _first_marker_offset(runner, headline) if runner else None
        if best_at is not None and runner_at is not None and best_at != runner_at:
            winner = best if best_at < runner_at else runner
            loser = runner if winner is best else best
            return FamilyVerdict(
                winner,
                max(best_score, runner_score),
                loser,
                min(best_score, runner_score),
                "matched_by_position",
                hint,
                hint_score,
            )
        return FamilyVerdict(None, best_score, runner, runner_score, "ambiguous",
                             hint, hint_score)

    return FamilyVerdict(best, best_score, runner, runner_score, "matched",
                         hint, hint_score)


_FAMILY_ORDER = {f: i for i, f in enumerate(TechniqueFamily)}


def unclassified(verdicts: Sequence[FamilyVerdict]) -> list[FamilyVerdict]:
    """The rows a Sonnet pass would have to resolve — option (2) of FLE-13's ladder."""
    return [v for v in verdicts if not v.classified]
