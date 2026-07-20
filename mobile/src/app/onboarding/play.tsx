// mobile/src/app/onboarding/play.tsx
// Wizard section 2 of 5 — "What can you play?"
//
// Voice contract: fletcher-identity.md
//   Heading: "What can you play?"
//   Body: "Songs you're solid on. Tempo held, chords clean. Type them out — one per line if you want."
//   Placeholder: "Sweet Home Chicago\nWonderwall\nAny song you can pick up cold"
//   CTA: "Continue"
//   Pattern: warm framing for the section intro. The "diagnosis" component of the
//   fletcher-identity.md pattern is not present here — this is pure information
//   gathering. Copy is terse, respects the user's competence (line 31).
//   No emojis. No exclamation points.
//
// D-03: SongInputArea reads/writes wizard.section.play in MMKV via 300ms debounce.
//   On resume, text is prefilled from MMKV automatically.
//   setLastSection('working-on') called in onContinue to update the resume pointer.
import React from 'react';
import { router } from 'expo-router';
import { FletcherIntroCard } from '../../components/FletcherIntroCard';
import { SongInputArea } from '../../components/SongInputArea';
import { setLastSection } from '../../api/mmkv';

export default function OnboardingPlay() {
  const onContinue = () => {
    // Advance the D-03 resume pointer before navigating.
    setLastSection('working-on');
    router.push('/onboarding/working-on');
  };

  return (
    <FletcherIntroCard
      heading="What can you play?"
      body="Songs you're solid on. Tempo held, chords clean. Type them out — one per line if you want."
      cta="Continue"
      onNext={onContinue}
      progress={{ current: 2, total: 5 }}
    >
      <SongInputArea
        section="play"
        placeholder={"Sweet Home Chicago\nWonderwall\nAny song you can pick up cold"}
      />
    </FletcherIntroCard>
  );
}
