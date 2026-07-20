// mobile/src/app/onboarding/working-on.tsx
// Wizard section 3 of 5 — "What are you working on?"
//
// Voice contract: fletcher-identity.md
//   Heading: "What are you working on?"
//   Body: "Songs you've almost got. The ones that are close but not clean yet."
//   Placeholder: "Little Wing\nWish You Were Here\nAnything mid-grind"
//   CTA: "Continue"
//   Pattern: terse, warm (line 31). "close but not clean yet" acknowledges the
//   grind without judgment — per line 73: "respect the grind."
//   No emojis. No exclamation points.
//
// D-03: SongInputArea reads/writes wizard.section.working-on in MMKV via 300ms debounce.
//   On resume, text is prefilled from MMKV automatically.
//   setLastSection('aspire') called in onContinue to update the resume pointer.
import React from 'react';
import { router } from 'expo-router';
import { FletcherIntroCard } from '../../components/FletcherIntroCard';
import { SongInputArea } from '../../components/SongInputArea';
import { setLastSection } from '../../api/mmkv';

export default function OnboardingWorkingOn() {
  const onContinue = () => {
    // Advance the D-03 resume pointer before navigating.
    setLastSection('aspire');
    router.push('/onboarding/aspire');
  };

  return (
    <FletcherIntroCard
      heading="What are you working on?"
      body="Songs you've almost got. The ones that are close but not clean yet."
      cta="Continue"
      onNext={onContinue}
      progress={{ current: 3, total: 5 }}
    >
      <SongInputArea
        section="working-on"
        placeholder={"Little Wing\nWish You Were Here\nAnything mid-grind"}
      />
    </FletcherIntroCard>
  );
}
