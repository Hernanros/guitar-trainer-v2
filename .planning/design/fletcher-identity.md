# Fletcher — Product & Visual Identity

**Locked:** 2026-07-19
**Consumed by:** Phase 2 discuss-phase, Phase 2 UI-phase (UI-SPEC.md generation), Phase 3 AI teacher voice
**Status:** Design intent captured; concrete visual + asset acquisition happens in Phase 2 UI-phase

---

## The reference

**Fletcher** is Terence Fletcher, the jazz teacher from *Whiplash* (2014), played by JK Simmons. He is:

- Demanding to the point of cruelty
- Musically precise ("Not my tempo.")
- Terrifying and transformative in equal measure
- The archetypal "difficult teacher who makes you better"

The app is named after him because the AI in Fletcher plays the same role — a teacher, not a trainer or an app. The reference is affectionate parody, not endorsement of the character's actual behavior. Fletcher-the-app should feel demanding-but-fair, sardonic, direct, never mean.

---

## Where the joke should land

The visual + verbal reference to Fletcher should appear in these places (in descending priority):

### 1. Onboarding "Meet your teacher" moment — **highest priority**

The single moment where the whole app's tone is established. First launch, before any real work. One screen, one image, one line of copy that lands the reference. Something like:

> "This is Fletcher. He teaches guitar. Some players think he's a lot. He is."
> [continue]

The visual here is the biggest asset ask.

### 2. Splash screen

Users see this 5–10 times a day. Text is enough — "Not my tempo." as tagline text on the splash lands the reference for anyone who's seen the film, feels neutral for those who haven't. Consider pairing with a stylized silhouette (bald head + suspended cymbal in shadow) once the icon-style is set.

### 3. Session rating micro-copy (Phase 3, but tone locks here)

When a user self-reports how a practice session went, Fletcher-the-teacher is the voice giving feedback. His vocabulary:

- "Rushing." (tempo too fast, timing off)
- "Dragging." (tempo too slow, behind the beat)
- "Not quite my tempo." (close but wrong)
- "Better." (progress — sparing praise, this is the max compliment)
- "Do it again." (rating implies retry)
- "That's the tempo." (rare — full approval)

These become the button labels or auto-response text on the session rating screen. Do NOT use exclamation points. Do NOT use emojis. Fletcher does not celebrate.

### 4. Empty states / edge cases

- Loading Song of the Day: `"..."` then `"Not quite my tempo."` if the request is slow
- Offline banner: `"Fletcher noticed you're offline. He'll wait."`
- Missed practice day: `"Fletcher noticed."` (nothing more)

### 5. App icon — **defer for now**

The tiny canvas is hard to render Simmons' features on. Ship Phase 1 with the current Expo default icon; commission or generate an app icon in a later phase once the visual language is settled. Options when we do:

- Simplified silhouette (bald head in profile, jazz-club lighting)
- Abstract musical symbol (single suspended cymbal, drum-kit shadow)
- Stylized "F" monogram in a demanding sans-serif

---

## Voice & tone rules

- **Demanding, not mean.** Fletcher pushes the player; he doesn't insult them personally.
- **Musical vocabulary.** Prefer jazz/rhythm terminology ("tempo", "time", "in the pocket") over generic praise ("great job!").
- **Sparing praise.** Approval is rare and understated. Never gushing.
- **Direct.** Fletcher does not explain himself. Copy should be terse.
- **Second person, present tense.** "You rushed." not "You were rushing" or "The tempo was off."
- **No emojis. No exclamation points. No !!.**
- **Never break character** unless it's a legitimate error state ("Server unreachable. Try again in a moment." — this is the system speaking, not Fletcher).

Contrast — what Fletcher would NEVER say:
- "Great practice! Keep it up! 🎸" (breaks tone completely)
- "You're doing amazing!" (Fletcher does not do amazing)
- "That's okay, we all make mistakes!" (definitively not Fletcher)

---

## Asset direction

**POC (through v1.0):**
- Use an **AI-generated stylized character portrait** for the onboarding "meet Fletcher" moment. Prompt direction: *"Portrait of a menacing bald music teacher in his early 50s, black shirt, glaring intently at camera, dim jazz-club lighting, dramatic contrast, painterly stylized illustration"*. Refine until the character reads as Fletcher-ish without being a direct copy of JK Simmons. Iterate 5–10 gens; pick the one that lands.
- Alternative if AI gen doesn't land: **silhouette approach** — pure black silhouette of the bald head + a suspended cymbal detail in the background. Reference without face. Legally safest.

**Shipped v1.x and beyond:**
- Move to a **commissioned original illustration** (~$100–300 range for a character portrait). Owned outright, no licensing risk. Style should match the AI-gen aesthetic if that worked in POC.
- Do NOT ship actual movie stills of JK Simmons at any point. Fair-use for parody is real but not bulletproof, and the risk/reward is bad — you'd take down a viral moment because of a takedown notice.

**App icon (whenever we tackle it):**
- Symbolic, not portrait. A face at 60px is either indistinct or generic. Prefer: bald-head silhouette in profile against a suspended-cymbal shadow, or the letter "F" in a demanding serif on a black-on-black background.
- Design at 1024px, verify legibility at 60px.

---

## Legal / IP caveats

- **JK Simmons' likeness is his property.** Nothing this document proposes uses his actual likeness in a commercial product. The "Fletcher-ish" character is inspired by the archetype (demanding music teacher) but is a distinct entity.
- **Whiplash is Sony Pictures / Blumhouse.** The character name "Fletcher" is not a Sony trademark (it's a common surname), but the character *as depicted in the film* is. Avoid explicit visual copies of Fletcher's on-screen appearance (specific clothing, specific poses from the film).
- **Quoting from the film:** short phrases like "Not my tempo" are unlikely to be treated as protected on their own (short, factual musical instruction), but pairing them with a JK-Simmons-recognizable portrait would tip toward infringement. Keep the quote as tagline, keep the visual character-stylized (not-Simmons).
- **When in doubt**: silhouette + text > face + text. The joke lands on the archetype, not the actor.

---

## What Phase 2 UI-phase needs to produce

When `/gsd-ui-phase 2` runs, it should output UI-SPEC.md entries covering:

1. **Onboarding "meet Fletcher" screen** — layout, image slot, copy, next-button behavior. Reference this file for the copy direction and asset acquisition path.
2. **Splash screen** — decide between "current Expo default", "text-only 'Not my tempo.' tagline", or "silhouette + tagline". Recommend the middle option for POC.
3. **Placeholder screens (Library, Toolkit)** — Fletcher-flavored "Coming soon" copy: `"Fletcher hasn't gotten to the library yet. Practice your song."` etc.
4. **Session rating buttons** — the Fletcher vocabulary above becomes the actual UI labels.
5. **Offline banner + error states** — Fletcher voice as documented above.
6. **App-icon decision** — defer explicitly to a later phase, do not block Phase 2 on it.

---

## Reference material for Phase 2 designer / prompt engineer

If commissioning or prompting for the Fletcher character:

- **Framings**: front-facing portrait, three-quarter, glare-down (all work; portrait most versatile)
- **Palette**: dark tones, warm accent (jazz-club amber), high contrast
- **Emotional register**: expectant + judgmental, not raging — this is Fletcher before the chair throw, not during
- **Age**: early-to-mid 50s
- **Details that read as "music teacher"**: black shirt (never a suit), sometimes visible drum-kit or piano in far background
- **Details to avoid**: obvious military/uniform elements, any smile, any warm expression

---

*Design intent locked; concrete visual specification to be produced by `/gsd-ui-phase 2` and iterated on with real asset gens.*
