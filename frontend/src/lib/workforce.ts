import type { Shift, Weekday, WorkArea, WorkStyle } from '../data/types';

/**
 * Display labels and option lists for the workforce layer.
 *
 * One place, because the same vocabulary appears in the preference form, on
 * the recommendation cards and inside the evidence panel — three surfaces that
 * must not drift into calling the same thing by two names. The stored values
 * are the lowercase codes; these are only ever what a human reads.
 */

export const SHIFT_LABEL: Record<Shift, string> = {
  morning: 'Morning',
  afternoon: 'Afternoon',
  evening: 'Evening',
  flexible: 'Flexible',
};

export const WORK_AREA_LABEL: Record<WorkArea, string> = {
  backend: 'Backend',
  frontend: 'Frontend',
  data: 'Data',
  devops: 'DevOps',
  testing: 'Testing',
};

export const WEEKDAY_LABEL: Record<Weekday, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
};

export const WORK_STYLE_LABEL: Record<WorkStyle, string> = {
  individual: 'Individually',
  collaborative: 'Collaboratively',
  mixed: 'A mix of both',
};

/** Turns a label map into the `{ value, label }` list the chip pickers take. */
function options<T extends string>(map: Record<T, string>): readonly { value: T; label: string }[] {
  return (Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));
}

export const SHIFT_OPTIONS = options(SHIFT_LABEL);
export const WORK_AREA_OPTIONS = options(WORK_AREA_LABEL);
export const WEEKDAY_OPTIONS = options(WEEKDAY_LABEL);
export const WORK_STYLE_OPTIONS = options(WORK_STYLE_LABEL);
