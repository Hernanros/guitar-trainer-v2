// mobile/src/app/onboarding/aspire.tsx
// Wizard section 4 of 5 — "What are you chasing?"
//
// Voice contract: fletcher-identity.md
//   Heading: "What are you chasing?"
//   Body: "The songs you're not there yet. Not close. But you'd like to be. Say it out loud — Fletcher will build toward it."
//   Placeholder: "Eruption\nPurple Haze\nEverything by Julian Lage"
//   CTA: "Continue"
//   Pattern: "Say it out loud — Fletcher will build toward it." is the confidence
//   signal (fletcher-identity.md line 78: sharp diagnosis → specific next step →
//   confidence signal). The confidence signal here is the promise: "build toward it."
//   No emojis. No exclamation points.
//
// Aspire deserves more vertical room per <specifics> — SongInputArea gets minHeight={200}.
//
// D-03: SongInputArea reads/writes wizard.section.aspire in MMKV via 300ms debounce.
//   setLastSection('preferences') called in onContinue to update the resume pointer.
import React from 'react';
import { router } from 'expo-router';
import { FletcherIntroCard } from '../../components/FletcherIntroCard';
import { SongInputArea } from '../../components/SongInputArea';
import { setLastSection } from '../../api/mmkv';

export default function OnboardingAspire() {
  const onContinue = () => {
    // Advance the D-03 resume pointer before navigating.
    setLastSection('preferences');
    router.push('/onboarding/preferences');
  };

  return (
    <FletcherIntroCard
      heading="What are you chasing?"
      body="The songs you're not there yet. Not close. But you'd like to be. Say it out loud — Fletcher will build toward it."
      cta="Continue"
      onNext={onContinue}
      progress={{ current: 4, total: 5 }}
    >
      {/* Larger input area: Aspire section carries the most emotional weight (per <specifics>). */}
      <SongInputArea
        section="aspire"
        placeholder={"Eruption\nPurple Haze\nEverything by Julian Lage"}
        minHeight={200}
      />
    </FletcherIntroCard>
  );
}
