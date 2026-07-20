// mobile/src/components/SongInputArea.tsx
// Multi-line free-text input for wizard song sections.
//
// Write-through to MMKV on every keystroke, debounced at 300ms (D-03).
// On mount: reads existing MMKV value and prefills — this is the resume-mid-flow
// behavior (D-03). Force-quit + reopen = text still there.
//
// T-02-02-03 mitigation: soft 4000-char cap per section.
// Silently truncates further input above the cap. Does not block the user — just
// prevents pathologically-large payloads to the bootstrap endpoint.
//
// TextInput props verified against Expo SDK 57 / RN 0.86 docs:
//   multiline — yes, standard across versions
//   autoCapitalize="none" — correct for free-text song title lists (D-05)
//   autoCorrect={false} — prevents song title mangling
//   textAlignVertical="top" — Android aligns text to top of multiline box
import React, { useEffect, useRef, useState } from 'react';
import { StyleSheet, TextInput, View } from 'react-native';
import { getWizardSection, setWizardSection, type WizardSection } from '../api/mmkv';

const CHAR_CAP = 4000; // T-02-02-03: soft DoS cap — silently truncates above this

interface SongInputAreaProps {
  /** MMKV key ('play' | 'working-on' | 'aspire') — must match Expo Router file names. */
  section: WizardSection;
  placeholder: string;
  /** Aspire section passes 200 for more vertical room; default 140. */
  minHeight?: number;
  /** Optional: notifies parent when text changes (used to enable Continue button). */
  onChangeText?: (text: string) => void;
}

export function SongInputArea({
  section,
  placeholder,
  minHeight = 140,
  onChangeText,
}: SongInputAreaProps) {
  // Prefill from MMKV on mount (D-03 resume behavior).
  const [text, setText] = useState<string>(() => getWizardSection(section));
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup debounce timer on unmount.
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const handleChange = (next: string) => {
    // T-02-02-03: truncate at cap boundary silently.
    const safe = next.length > CHAR_CAP ? next.slice(0, CHAR_CAP) : next;
    setText(safe);
    onChangeText?.(safe);
    // Debounced MMKV write — avoids thrashing on rapid keystrokes.
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setWizardSection(section, safe), 300);
  };

  return (
    <View style={[styles.card, { minHeight }]}>
      <TextInput
        style={styles.input}
        multiline
        value={text}
        onChangeText={handleChange}
        placeholder={placeholder}
        placeholderTextColor="#666"
        autoCapitalize="none"
        autoCorrect={false}
        textAlignVertical="top"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  // Card style: mirrors techniqueCard from (tabs)/index.tsx — dark card + orange border-left.
  card: {
    backgroundColor: '#242424',
    borderRadius: 10,
    padding: 14,
    marginBottom: 16,
    borderLeftWidth: 3,
    borderLeftColor: '#E07B39',
  },
  input: {
    color: '#F5F5F5',
    fontSize: 16,
    lineHeight: 22,
    minHeight: 120,
  },
});
