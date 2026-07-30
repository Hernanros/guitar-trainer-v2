// mobile/src/utils/quota.ts
// Phase 4 quota helper — computes days until the breakdown quota resets.
//
// Used by SongOfDayCard (chip copy "Come back in {N} days") and BreakdownErrorCard
// (BREAKDOWN_CAPPED variant body copy). Derived client-side from server-authoritative
// resets_at ISO string so the message stays live if the user leaves the app open.

/**
 * Returns the number of whole days until the quota window resets.
 *
 * Uses Math.ceil so "23 hours left" rounds up to 1 day, matching the
 * "Come back in {N} days" copy intent. Returns 0 for null/undefined/invalid
 * input or if the date is already in the past.
 *
 * @param resets_at ISO datetime string from TodaySongResponse.breakdown_quota.resets_at
 */
export function daysUntilReset(resets_at: string | null | undefined): number {
  if (!resets_at) return 0;
  const ms = Date.parse(resets_at);
  if (isNaN(ms)) return 0;
  return Math.max(0, Math.ceil((ms - Date.now()) / (1000 * 60 * 60 * 24)));
}
