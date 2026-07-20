// mobile/src/app/onboarding/index.tsx
// Welcome screen — Fletcher joke-landing moment (02-02).
// Replaces the 02-01 dev placeholder entirely.
//
// Voice contract: fletcher-identity.md lines 87-93.
//   Heading: "Meet Fletcher."
//   Body: "He's the teacher Fletcher should have been. Five short questions. Then we practice."
//   CTA: "Start"
//   Pattern: warm framing (no user output to diagnose yet at this step — the "diagnosis"
//   component of the fletcher-identity.md pattern becomes a warm framing on Welcome).
//   No emojis. No exclamation points. Second person.
//
// D-03 resume-mid-flow: on mount, reads getLastSection(). If the user previously
//   reached a section past 'index', router.replace() takes them directly there
//   before any render paint (no Welcome-screen flicker on resume).
//   MMKV flag approach chosen over URL search params: survives app restarts and
//   is consistent with how wizard state is managed throughout the flow.
//   Resume banner ("Welcome back. Picking up where you left off.") deferred to a
//   polish pass — SongInputArea prefill is the visible resume signal.
import React, { useEffect } from 'react';
import { router } from 'expo-router';
import { FletcherIntroCard } from '../../components/FletcherIntroCard';
import { getLastSection, setLastSection } from '../../api/mmkv';

export default function OnboardingWelcome() {
  useEffect(() => {
    // D-03: resume mid-flow. Router.replace fires before the component renders,
    // so the user never sees the Welcome screen on a resume path.
    const last = getLastSection();
    if (last && last !== 'index') {
      router.replace(`/onboarding/${last}` as Parameters<typeof router.replace>[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onStart = () => {
    // Record that the user is advancing to 'play' for D-03 resume tracking.
    setLastSection('play');
    router.push('/onboarding/play');
  };

  return (
    <FletcherIntroCard
      heading="Meet Fletcher."
      body="He's the teacher Fletcher should have been. Five short questions. Then we practice."
      cta="Start"
      onNext={onStart}
      progress={{ current: 1, total: 5 }}
    />
  );
}
