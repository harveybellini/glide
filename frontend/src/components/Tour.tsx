import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { TourStep } from "../tour";

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

interface Props {
  step: TourStep;
  stepNumber: number;
  totalSteps: number;
  isFirst: boolean;
  isLast: boolean;
  busy: boolean;
  primaryLabel: string;
  onPrimary: () => void;
  onBack: () => void;
  /** Ends the tour: the skip button, the close button, and Escape all use it. */
  onClose: () => void;
}

const VIEWPORT_MARGIN = 16;
const TARGET_GAP = 14;
const RING_PADDING = 6;

/**
 * The spotlight ring and the step card.
 *
 * The card is deliberately not modal: the darkened page still takes clicks, so
 * the visitor can use the control the tour is describing instead of only
 * reading about it. Every step targets a real element by selector, which means
 * the ring can never point at something that is not there — if nothing matches,
 * the tour renders nothing rather than an empty rectangle.
 */
export default function Tour({
  step,
  stepNumber,
  totalSteps,
  isFirst,
  isLast,
  busy,
  primaryLabel,
  onPrimary,
  onBack,
  onClose,
}: Props) {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [targetBox, setTargetBox] = useState<Box | null>(null);
  const [cardBox, setCardBox] = useState<Box>({
    top: 0,
    left: 0,
    width: 348,
    height: 190,
  });
  // A callback ref rather than a ref object: the card unmounts for a frame
  // whenever a step changes (the new target has not been measured yet), so the
  // measurement has to follow the node in and out rather than run once per
  // step and find nothing.
  const [cardNode, setCardNode] = useState<HTMLDivElement | null>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  // The document-level Escape listener is registered once, so it reads the
  // latest handler through a ref rather than being torn down every render.
  const closeRef = useRef(onClose);
  const narrow = useMediaQuery("(max-width: 680px)");
  const calm = useMediaQuery("(prefers-reduced-motion: reduce)");

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  // Find the element this step is about, and bring it into view when it is
  // hidden. A sticky element (the sidebar) is always on screen, so asking the
  // browser to centre it would scroll the page for no reason. On a phone the
  // card takes the bottom half, so the target belongs near the top instead.
  useEffect(() => {
    const found =
      step.selectors
        .map((selector) => document.querySelector<HTMLElement>(selector))
        .find((element): element is HTMLElement => element !== null) ?? null;
    setTarget(found);
    setTargetBox(null);
    if (!found) {
      return;
    }
    const box = found.getBoundingClientRect();
    const visibleBelow = narrow
      ? window.innerHeight * 0.45
      : window.innerHeight - RING_PADDING;
    const onScreen =
      box.top >= RING_PADDING && box.bottom <= visibleBelow;
    if (!onScreen) {
      found.scrollIntoView({
        block: narrow ? "start" : "center",
        inline: "nearest",
        behavior: calm ? "auto" : "smooth",
      });
    }
  }, [step, calm, narrow]);

  // Follow the target while the page scrolls, the window resizes, or content
  // elsewhere on the page changes the layout under it.
  useLayoutEffect(() => {
    if (!target) {
      return;
    }
    const measure = () => {
      const next = target.getBoundingClientRect();
      setTargetBox((current) =>
        current &&
        nearly(current.top, next.top) &&
        nearly(current.left, next.left) &&
        nearly(current.width, next.width) &&
        nearly(current.height, next.height)
          ? current
          : {
              top: next.top,
              left: next.left,
              width: next.width,
              height: next.height,
            },
      );
    };
    measure();
    window.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(target);
    observer.observe(document.body);
    return () => {
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      observer.disconnect();
    };
  }, [target]);

  // The card moves to the side of the target that has room, which depends on
  // how tall its own copy is once it has rendered.
  useLayoutEffect(() => {
    if (!cardNode) {
      return;
    }
    const measure = () =>
      setCardBox((current) => {
        const width = cardNode.offsetWidth;
        const height = cardNode.offsetHeight;
        return current.width === width && current.height === height
          ? current
          : { ...current, width, height };
      });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(cardNode);
    return () => observer.disconnect();
  }, [cardNode]);

  // Move focus into the card as each step opens, so a keyboard user is taken
  // along the tour too (the ring is decoration and cannot hold focus). The
  // card mounts a frame after the step changes, so the effect waits for the
  // node rather than for the step alone.
  useEffect(() => {
    primaryRef.current?.focus({ preventScroll: true });
  }, [cardNode, step]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeRef.current();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const handleKeys = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowRight" && !busy) {
      event.preventDefault();
      onPrimary();
    } else if (event.key === "ArrowLeft" && !isFirst) {
      event.preventDefault();
      onBack();
    }
  };

  if (!targetBox) {
    return null;
  }

  const ring = ringFor(targetBox);
  const placement = placementFor(targetBox, cardBox, narrow);

  return createPortal(
    <div className="tour-layer">
      {ring && (
        <div
          className="tour-ring"
          aria-hidden="true"
          style={{
            top: `${ring.top}px`,
            left: `${ring.left}px`,
            width: `${ring.width}px`,
            height: `${ring.height}px`,
          }}
        />
      )}
      <div
        ref={setCardNode}
        className="tour-card"
        style={placement}
        role="dialog"
        aria-modal="false"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
        onKeyDown={handleKeys}
      >
        <div className="tour-head">
          <p className="eyebrow">
            STEP {stepNumber} OF {totalSteps}
          </p>
          <button
            type="button"
            className="tour-close"
            onClick={onClose}
            aria-label="Close the tour"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
        <p className="sr-only" aria-live="polite">
          Step {stepNumber} of {totalSteps}: {step.title}
        </p>
        <h2 id="tour-title">{step.title}</h2>
        <p id="tour-body">{step.body}</p>
        {step.hint && <p className="tour-hint">{step.hint}</p>}
        <div className="tour-actions">
          {!isFirst && (
            <button type="button" className="tour-back" onClick={onBack}>
              Back
            </button>
          )}
          <button
            ref={primaryRef}
            type="button"
            className="primary"
            onClick={onPrimary}
            disabled={busy}
          >
            {busy ? "Working…" : primaryLabel}
          </button>
          <button type="button" className="tour-skip" onClick={onClose}>
            {isLast ? "Close" : "Skip the tour"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** The visible slice of the target, padded so the ring clears its border. */
function ringFor(box: Box): Box | null {
  const left = Math.max(0, box.left - RING_PADDING);
  const top = Math.max(0, box.top - RING_PADDING);
  const right = Math.min(window.innerWidth, box.left + box.width + RING_PADDING);
  const bottom = Math.min(window.innerHeight, box.top + box.height + RING_PADDING);
  if (right - left < 8 || bottom - top < 8) {
    return null;
  }
  return { top, left, width: right - left, height: bottom - top };
}

/**
 * Where to put the card: under the target when there is room, above it when
 * there is not, and against the bottom of the window when the target is taller
 * than most of the viewport. On a phone it docks to whichever end of the
 * screen the target is not using, so the spotlight is never covered.
 */
function placementFor(
  target: Box,
  card: Box,
  narrow: boolean,
): React.CSSProperties | undefined {
  const { innerWidth: width, innerHeight: height } = window;
  if (narrow) {
    const cardHeight = card.height || 190;
    const margin = 12;
    const targetMiddle = target.top + target.height / 2;
    return {
      top: `${Math.round(
        targetMiddle < height / 2 ? height - cardHeight - margin : margin,
      )}px`,
      left: `${margin}px`,
      right: `${margin}px`,
      bottom: "auto",
    };
  }
  const cardWidth = card.width || 348;
  const cardHeight = card.height || 190;
  const roomBelow = height - (target.top + target.height) - TARGET_GAP - VIEWPORT_MARGIN;
  const roomAbove = target.top - TARGET_GAP - VIEWPORT_MARGIN;
  const tallTarget = target.height > height * 0.55;
  let top: number;
  if (tallTarget || (roomBelow < cardHeight && roomAbove < cardHeight)) {
    top = height - cardHeight - VIEWPORT_MARGIN;
  } else {
    top =
      roomBelow >= cardHeight
        ? target.top + target.height + TARGET_GAP
        : target.top - cardHeight - TARGET_GAP;
  }
  const centred = target.left + target.width / 2 - cardWidth / 2;
  const left = Math.min(
    Math.max(centred, VIEWPORT_MARGIN),
    Math.max(VIEWPORT_MARGIN, width - cardWidth - VIEWPORT_MARGIN),
  );
  // Belt and braces: a card measured a frame late must still not hang off the
  // bottom of the window.
  const clampedTop = Math.min(
    Math.max(top, VIEWPORT_MARGIN),
    Math.max(VIEWPORT_MARGIN, height - cardHeight - VIEWPORT_MARGIN),
  );
  return {
    top: `${Math.round(clampedTop)}px`,
    left: `${Math.round(left)}px`,
  };
}

function nearly(left: number, right: number): boolean {
  return Math.abs(left - right) < 0.5;
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => window.matchMedia?.(query).matches ?? false,
  );
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}
