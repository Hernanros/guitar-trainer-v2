# server/app/sessions/ — the deterministic session engine (FLE-9).
#
# Implements the FLE-4 design spec "Session Structure & Drill Taxonomy",
# generator version session-gen/1.0.0.
#
# Hard architectural line, carried from five prior phases and restated in the
# spec: NO LLM AT SESSION-GENERATION TIME. Sonnet writes drills; plain code
# assembles sessions. Everything in this package is a pure function of a DB
# snapshot, so a session is instant, free, reproducible and debuggable.
