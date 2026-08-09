import { motion } from 'framer-motion';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChoiceChips, MultiChoiceChips } from '../components/ChoiceChips';
import { ErrorPanel, LoadingPanel } from '../components/Feedback';
import { NumberField, TagField, TextField } from '../components/Fields';
import { GlassCard } from '../components/GlassCard';
import { ScreenHeader } from '../components/ScreenHeader';
import { getMyProfile, saveMyProfile } from '../data/api';
import type { Shift, Weekday, WorkArea, WorkStyle, WorkforceProfile } from '../data/types';
import { stagger } from '../lib/motion';
import { useAsync } from '../lib/useAsync';
import {
  SHIFT_OPTIONS,
  WEEKDAY_OPTIONS,
  WORK_AREA_OPTIONS,
  WORK_STYLE_OPTIONS,
} from '../lib/workforce';

/**
 * The employee's own record — and the only screen in the product where a
 * person edits something about themselves.
 *
 * Everything here is VOLUNTEERED. Nothing on this screen was observed, inferred
 * from a commit, or read out of the event log, and the copy says so beside the
 * save button rather than in a footer nobody reads. Blank is a real answer: a
 * field left empty means no preference, never "no data", because a tool that
 * treats silence as a gap pressures people into filling it in.
 *
 * What is deliberately absent: any figure about this person's cost, output,
 * or standing relative to anyone else. Those do not exist in this layer, and
 * an employee should not learn them about themselves from a staffing tool.
 */

type SaveState =
  | { phase: 'idle' }
  | { phase: 'saving' }
  | { phase: 'saved'; at: string }
  | { phase: 'failed'; message: string };

export function EmployeeProfile() {
  const loaded = useAsync<WorkforceProfile>(getMyProfile, []);
  const [profile, setProfile] = useState<WorkforceProfile | null>(null);
  const [save, setSave] = useState<SaveState>({ phase: 'idle' });

  // Seeded once from the stored record; edits live here afterwards.
  useEffect(() => {
    if (loaded.status === 'ready' && profile === null) setProfile(loaded.data);
  }, [loaded, profile]);

  /** Any edit invalidates a previous "Saved" — the badge must not outlive it. */
  const update = useCallback((patch: Partial<WorkforceProfile>) => {
    setProfile((p) => (p ? { ...p, ...patch } : p));
    setSave((s) => (s.phase === 'saved' ? { phase: 'idle' } : s));
  }, []);

  const onSave = useCallback(async () => {
    if (!profile) return;
    setSave({ phase: 'saving' });
    try {
      const result = await saveMyProfile(profile);
      setSave(
        result.saved
          ? { phase: 'saved', at: result.savedAt }
          : { phase: 'failed', message: 'The server did not accept this profile.' },
      );
    } catch (err) {
      setSave({ phase: 'failed', message: err instanceof Error ? err.message : String(err) });
    }
  }, [profile]);

  return (
    <>
      <ScreenHeader
        eyebrow="My profile"
        title="How you would like to work"
        lede="You choose what to share. Every field is optional, and blank means no preference rather than no answer."
      />

      <div className="mx-auto max-w-[1000px] px-6 pt-6 pb-14 sm:px-10">
        {loaded.status === 'error' ? (
          <ErrorPanel error={loaded.error} />
        ) : !profile ? (
          <LoadingPanel label="Loading your profile" />
        ) : (
          <motion.div
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-6"
          >
            <div
              className="rounded-xl border px-5 py-4"
              style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.5)' }}
            >
              <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                <span className="font-semibold text-[var(--text-primary)]">
                  This is your record, not a measurement of you.
                </span>{' '}
                Everything below is what you told us. It is stored against your employee record and
                is never joined to engineering activity data — no commit, review or cost figure is
                attached to your name anywhere in this product.
              </p>
            </div>

            {/* 1 — who you are professionally */}
            <GlassCard className="p-5" animate={false}>
              <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                Skills and experience
              </h2>

              <div className="mt-5 grid gap-x-8 gap-y-6 md:grid-cols-2">
                <TextField
                  label="Primary role"
                  value={profile.primaryRole}
                  onChange={(v) => update({ primaryRole: v })}
                  placeholder="Backend Engineer"
                />
                <NumberField
                  label="Experience"
                  value={profile.experienceYears}
                  onChange={(v) => update({ experienceYears: v })}
                  min={0}
                  max={50}
                  suffix="years"
                />
                <div className="md:col-span-2">
                  <TagField
                    label="Skills"
                    hint="press enter to add"
                    value={profile.skills}
                    onChange={(v) => update({ skills: v })}
                    placeholder="Java, Kafka, Distributed Systems…"
                  />
                </div>
                <div className="md:col-span-2">
                  <TagField
                    label="Training and certifications"
                    hint="optional"
                    value={profile.certifications}
                    onChange={(v) => update({ certifications: v })}
                    placeholder="AWS Solutions Architect…"
                  />
                </div>
              </div>
            </GlassCard>

            {/* 2 — where you are and where you would go */}
            <GlassCard className="p-5" animate={false}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                  Location and mobility
                </h2>
                <p className="text-[11.5px] text-[var(--text-muted)]">
                  Declining relocation is never held against you.
                </p>
              </div>

              <div className="mt-5 grid gap-x-8 gap-y-6 md:grid-cols-2">
                <TextField
                  label="Current location"
                  value={profile.currentLocation}
                  onChange={(v) => update({ currentLocation: v })}
                  placeholder="Gurgaon"
                />
                <TagField
                  label="Preferred locations"
                  hint="press enter to add"
                  value={profile.preferredLocations}
                  onChange={(v) => update({ preferredLocations: v })}
                  placeholder="Gurgaon, Hyderabad…"
                />
                <ChoiceChips
                  legend="Open to relocation?"
                  options={
                    [
                      { value: 'yes', label: 'Yes' },
                      { value: 'no', label: 'No' },
                    ] as const
                  }
                  value={profile.openToRelocation ? 'yes' : 'no'}
                  onChange={(v) => update({ openToRelocation: v === 'yes' })}
                />
                {/* Only asked once the answer above makes it meaningful. */}
                {profile.openToRelocation ? (
                  <TagField
                    label="Preferred relocation locations"
                    hint="press enter to add"
                    value={profile.preferredRelocationLocations}
                    onChange={(v) => update({ preferredRelocationLocations: v })}
                    placeholder="Bangalore, Hyderabad…"
                  />
                ) : (
                  <div className="flex flex-col justify-end">
                    <p className="text-[12px] leading-relaxed text-[var(--text-muted)]">
                      No openings requiring a move will be proposed to you, and none will be
                      simulated against your record.
                    </p>
                  </div>
                )}
              </div>
            </GlassCard>

            {/* 3 — how and when you want to work */}
            <GlassCard className="p-5" animate={false}>
              <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                Preferences and availability
              </h2>

              <div className="mt-5 grid gap-x-8 gap-y-6 md:grid-cols-2">
                <ChoiceChips
                  legend="Preferred shift"
                  options={SHIFT_OPTIONS}
                  value={profile.preferredShift}
                  onChange={(v: Shift) => update({ preferredShift: v })}
                />
                <MultiChoiceChips
                  legend="Interested work areas"
                  hint="pick any"
                  options={WORK_AREA_OPTIONS}
                  value={profile.workAreas}
                  onChange={(v: WorkArea[]) => update({ workAreas: v })}
                />
                <div className="md:col-span-2">
                  <MultiChoiceChips
                    legend="Availability"
                    hint="pick any"
                    options={WEEKDAY_OPTIONS}
                    value={profile.availability}
                    onChange={(v: Weekday[]) => update({ availability: v })}
                  />
                </div>
                <ChoiceChips
                  legend="You work best"
                  options={WORK_STYLE_OPTIONS}
                  value={profile.workStyle}
                  onChange={(v: WorkStyle) => update({ workStyle: v })}
                />
                <ChoiceChips
                  legend="Open to other teams?"
                  options={
                    [
                      { value: 'yes', label: 'Yes' },
                      { value: 'no', label: 'No' },
                    ] as const
                  }
                  value={profile.openToOtherTeams ? 'yes' : 'no'}
                  onChange={(v) => update({ openToOtherTeams: v === 'yes' })}
                />
                <div className="md:col-span-2">
                  <TagField
                    label="Preferred projects and components"
                    hint="press enter to add"
                    value={profile.preferredComponents}
                    onChange={(v) => update({ preferredComponents: v })}
                    placeholder="apache/kafka/clients…"
                  />
                </div>
                <TextField
                  label="Availability for reassignment"
                  value={profile.availableFrom}
                  onChange={(v) => update({ availableFrom: v })}
                  placeholder="From 1 March"
                />
              </div>

              <div
                className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-4"
                style={{ borderColor: 'var(--border)' }}
              >
                <button
                  type="button"
                  onClick={() => void onSave()}
                  disabled={save.phase === 'saving'}
                  className="h-9 rounded-lg border px-5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  style={{
                    color: 'var(--bg-page)',
                    background: 'var(--ui)',
                    borderColor: 'var(--ui)',
                  }}
                >
                  {save.phase === 'saving' ? 'Saving…' : 'Save preferences'}
                </button>

                {save.phase === 'saved' && (
                  <span className="text-[12px]" style={{ color: 'var(--teal)' }}>
                    Saved at {new Date(save.at).toLocaleTimeString()}.{' '}
                    <Link to="/me/opportunities" className="underline underline-offset-2">
                      See matching opportunities
                    </Link>
                    .
                  </span>
                )}
                {save.phase === 'failed' && (
                  <span className="text-[12px]" style={{ color: 'var(--coral)' }}>
                    {save.message}
                  </span>
                )}
                {save.phase === 'idle' && (
                  <span className="text-[12px] text-[var(--text-muted)]">
                    Stored against your employee record, not the event log.
                  </span>
                )}
              </div>
            </GlassCard>
          </motion.div>
        )}
      </div>
    </>
  );
}
