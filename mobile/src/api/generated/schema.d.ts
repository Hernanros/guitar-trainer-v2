/**
 * Baseline OpenAPI TypeScript types — generated from FastAPI Pydantic models.
 *
 * THIS FILE IS A COMMITTED BASELINE. Run `npm run codegen:local` (with the FastAPI server
 * running at localhost:8000) to regenerate the full schema. Codegen output overwrites this
 * file in place — the shape is identical, the generated version has fuller JSDoc.
 *
 * Matches server/app/models/song.py — D-03 semantic music JSON (Phase 3-ready shape).
 *
 * If this file diverges from the server schema, run codegen to re-sync.
 */

export interface components {
  schemas: {
    /** A single picked note on the guitar. */
    Note: {
      /** string: 1 = high e … 6 = low E */
      string: number;
      /** fret: 0 = open */
      fret: number;
      /** duration: "quarter" | "eighth" | "half" | "whole" | "sixteenth" */
      duration: string;
    };
    /** A rhythmic beat holding one or more simultaneous notes. */
    Beat: {
      notes: components['schemas']['Note'][];
    };
    /** A musical measure with a list of beats. */
    Measure: {
      beats: components['schemas']['Beat'][];
      /** time_signature e.g. "4/4", "3/4", "6/8" */
      time_signature: string;
    };
    /** Full tab notation as a sequence of measures. */
    Tab: {
      measures: components['schemas']['Measure'][];
      /** Standard low-to-high tuning */
      tuning: string[];
    };
    /** Finger placement for a single string in a chord diagram. */
    ChordPosition: {
      /** string: 1 = high e … 6 = low E */
      string: number;
      /** fret: 0 = open, -1 = muted */
      fret: number;
      /** 1=index, 2=middle, 3=ring, 4=pinky; null = open/muted */
      finger: number | null;
    };
    /** A chord diagram with all string positions. */
    Chord: {
      name: string;
      positions: components['schemas']['ChordPosition'][];
      /** Fret number for full/partial barre; null if none */
      barre_fret: number | null;
      /** Starting fret of diagram window (needed for SVG y-offset) */
      base_fret: number;
    };
    /** A technique tip or instructional callout. */
    TechniqueNote: {
      heading: string;
      body: string;
    };
    /** Full Phase 3-ready song breakdown (D-01). */
    Breakdown: {
      tab: components['schemas']['Tab'];
      chords: components['schemas']['Chord'][];
      technique_notes: components['schemas']['TechniqueNote'][];
    };
    /** Top-level API response for GET /api/v1/song-of-day. */
    SongResponse: {
      id: number;
      title: string;
      artist: string;
      genre: string;
      /** "intermediate" | "advanced" */
      difficulty: string;
      bpm: number;
      key: string;
      breakdown: components['schemas']['Breakdown'];
    };
  };
}
