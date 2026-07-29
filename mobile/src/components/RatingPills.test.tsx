// mobile/src/components/RatingPills.test.tsx
// Tests for RatingPills component (Slice C — UI-SPEC §5).
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { RatingPills } from './RatingPills';

describe('RatingPills', () => {
  it('renders 3 pills with locked UI-SPEC §5 copy in order', async () => {
    const onSelect = jest.fn();
    const { getByText } = await render(<RatingPills onSelect={onSelect} />);

    expect(getByText('Not my tempo')).toBeTruthy();
    expect(getByText('Getting closer')).toBeTruthy();
    expect(getByText("That's what I'm looking for")).toBeTruthy();
  });

  it('pills have accessibilityRole="button" (not "radio")', async () => {
    const onSelect = jest.fn();
    const { getAllByRole } = await render(<RatingPills onSelect={onSelect} />);

    const buttons = getAllByRole('button');
    expect(buttons).toHaveLength(3);
  });

  it('calls onSelect with correct rating when a pill is pressed', async () => {
    const onSelect = jest.fn();
    const { getByText } = await render(<RatingPills onSelect={onSelect} />);

    fireEvent.press(getByText('Getting closer'));
    expect(onSelect).toHaveBeenCalledWith('getting_closer');
  });

  it('with selectedRating set, active pill has selected accessibilityState', async () => {
    const onSelect = jest.fn();
    const { getByLabelText } = await render(
      <RatingPills onSelect={onSelect} selectedRating="getting_closer" />,
    );

    const activeBtn = getByLabelText('Getting closer');
    expect(activeBtn.props.accessibilityState?.selected).toBe(true);
  });

  it('with selectedRating set, non-active pills are disabled', async () => {
    const onSelect = jest.fn();
    const { getByLabelText } = await render(
      <RatingPills onSelect={onSelect} selectedRating="getting_closer" />,
    );

    const notMyTempo = getByLabelText('Not my tempo');
    expect(notMyTempo.props.accessibilityState?.disabled).toBe(true);
  });

  it('with disabled=true, all pills are non-interactive', async () => {
    const onSelect = jest.fn();
    const { getAllByRole } = await render(<RatingPills onSelect={onSelect} disabled />);

    const buttons = getAllByRole('button');
    buttons.forEach((btn: { props: { accessibilityState?: { disabled?: boolean } } }) => {
      expect(btn.props.accessibilityState?.disabled).toBe(true);
    });
  });

  it('with disabled=true, pressing a pill does not call onSelect', async () => {
    const onSelect = jest.fn();
    const { getByText } = await render(<RatingPills onSelect={onSelect} disabled />);

    fireEvent.press(getByText('Not my tempo'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('with selectedRating set, pressing any pill does not call onSelect again', async () => {
    const onSelect = jest.fn();
    const { getByText } = await render(
      <RatingPills onSelect={onSelect} selectedRating="getting_closer" />,
    );

    fireEvent.press(getByText('Not my tempo'));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
