import Brand from "./Brand";

interface Props {
  /** What the app is doing, e.g. "Opening your day…". */
  label: string;
}

/**
 * Branded loading surface.
 *
 * Shown while the app resolves the session and loads a connected day, so a
 * returning user never sees the marketing landing page flash past before the
 * day view replaces it. The skeleton matches the timeline it is standing in
 * for; `prefers-reduced-motion` stops the shimmer (see styles.css).
 */
export default function BootScreen({ label }: Props) {
  return (
    <main className="boot" aria-busy="true">
      <div className="boot-inner">
        <h1 aria-label="Glide">
          <Brand />
        </h1>
        <p className="boot-status" role="status" aria-live="polite">
          <span className="boot-spinner" aria-hidden="true" />
          {label}
        </p>
        <div className="boot-skeleton" aria-hidden="true">
          <div className="boot-row boot-row-title" />
          <div className="boot-row" />
          <div className="boot-row boot-row-travel" />
          <div className="boot-row" />
          <div className="boot-row boot-row-short" />
        </div>
      </div>
    </main>
  );
}
