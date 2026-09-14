/**
 * The guided tour a first-time visitor sees.
 *
 * Every step points at a control that already exists on the page, so the tour
 * cannot drift away from the product the way a written script does: if the
 * button below is renamed, the step is wrong and the browser check that walks
 * this flow says so. Steps declare the screen they belong to, which is what
 * lets a returning visitor who already has a day open start inside the
 * workspace instead of being sent back to the landing page.
 */

// Bumping the suffix deliberately replays the tour for everyone; a small copy
// change is not a reason to interrupt a returning visitor.
// v2 reframes the tour around the background agent. Bumping the suffix
// deliberately replays the tour for everyone who saw the old manual-first
// wording.
export const TOUR_STORAGE_KEY = "glide-tour-v2";

/** Which screen a step belongs to. The tour skips steps for the other one. */
export type TourStage = "landing" | "day";

/**
 * Work the tour performs on the visitor's behalf, so the step after it lands
 * on a day that has actually changed. App maps these to its own handlers.
 */
export type TourActionId = "start-sample" | "run-check";

export interface TourStep {
  id: string;
  stage: TourStage;
  /**
   * First selector that matches wins. A later selector is a fallback for a
   * richer target that only exists once the day has been checked (a travel
   * block appears after the first run, the timeline itself always does).
   */
  selectors: string[];
  title: string;
  body: string;
  /** Nudge shown when the visitor can click the highlighted control itself. */
  hint?: string;
  action?: TourActionId;
  actionLabel?: string;
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: "start-sample",
    stage: "landing",
    selectors: ['[data-tour="start-sample"]'],
    title: "Start with a sample day",
    body: "Glide reads a day, works out the driving time between appointments, and reserves it. Start the sample and the agent keeps watching it on its own; the sample is fictional and its routes are simulated, so there is nothing real to undo.",
    hint: "The highlighted button is the only thing you need on this page.",
    action: "start-sample",
    actionLabel: "Start the sample day",
  },
  {
    id: "background",
    stage: "day",
    selectors: ['[data-tour="background"]'],
    title: "It works while you are away",
    body: "This strip is the agent's pulse: the last check, the next one, and how many ran while you were not looking. Scheduled checks run in the background even with the browser closed.",
    hint: "Stop watching here whenever you want; nothing already planned is removed.",
  },
  {
    id: "recheck",
    stage: "day",
    selectors: ['[data-tour="recheck"]'],
    title: "Recheck now, if you want it sooner",
    body: "The agent schedules its own checks; this button just runs one immediately. It reads the appointments, measures each drive, and writes the travel time into the calendar without editing the appointments themselves.",
    hint: "The scheduled check takes over again as soon as this one finishes.",
    action: "run-check",
    actionLabel: "Run a check now",
  },
  {
    id: "travel",
    stage: "day",
    selectors: ['[data-tour="travel-block"]', '[data-tour="timeline"]'],
    title: "See the time you were missing",
    body: "The green blocks are the journeys Glide added: drive time plus the arrival buffer you set. Everything in ivory is yours, untouched.",
  },
  {
    id: "decisions",
    stage: "day",
    // The decision card when there is one, and the guidance column it appears
    // in when the day has nothing to decide.
    selectors: ['[data-tour="decision"]', '[data-tour="guidance"]'],
    title: "When it does not fit, you decide",
    body: "If a journey needs more time than the day allows, a card appears here with the shortfall spelled out in minutes and only the actions that are safe to take. Glide asks instead of guessing.",
  },
  {
    id: "activity",
    stage: "day",
    selectors: ['[data-tour="activity"]'],
    title: "Every change is on the record",
    body: "Activity lists what Glide did to the calendar, so a repeat check that finds nothing new stays quiet instead of writing the same blocks twice.",
  },
  {
    id: "controls",
    stage: "day",
    selectors: ['[data-tour="controls"]'],
    title: "You keep the final say",
    body: "Pause automation whenever you like, reset the sample day, or open Settings for the arrival buffer, start address, and time zone. Show me around replays this tour.",
  },
];

/** Index of the first step that belongs to `stage`, or 0 if there is none. */
export function firstStepForStage(stage: TourStage): number {
  const index = TOUR_STEPS.findIndex((step) => step.stage === stage);
  return index === -1 ? 0 : index;
}

/**
 * The step to show for `stage`, keeping the current one when it already
 * matches. A visitor whose calendar is already open enters at the first
 * workspace step; a visitor still on the landing page enters at the top.
 */
export function stepForStage(current: number, stage: TourStage): number {
  if (TOUR_STEPS[current]?.stage === stage) {
    return current;
  }
  const ahead = TOUR_STEPS.findIndex(
    (step, index) => index > current && step.stage === stage,
  );
  return ahead === -1 ? firstStepForStage(stage) : ahead;
}
