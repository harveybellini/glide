import Brand from "./Brand";
import ConnectionStatus from "./ConnectionStatus";
import VersionBadge from "./VersionBadge";

interface Props {
  busy: boolean;
  error: string | null;
  onStart: () => void;
  onDisconnected: () => void;
  onStartTour: (trigger?: HTMLElement) => void;
}

export default function Welcome({
  busy,
  error,
  onStart,
  onDisconnected,
  onStartTour,
}: Props) {
  return (
    <main className="landing" aria-busy={busy}>
      <a className="skip-link" href="#get-started">Skip to get started</a>
      <header className="landing-header">
        <h1 aria-label="Glide"><Brand /></h1>
        <span className="header-note">A little room to breathe.</span>
        <div className="landing-links">
          <button
            type="button"
            className="text-link"
            onClick={(event) => onStartTour(event.currentTarget)}
          >
            Show me around
          </button>
          <a href="#how-it-works" className="text-link">How it works <span aria-hidden="true">↗</span></a>
        </div>
      </header>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy" id="get-started" tabIndex={-1}>
          <p className="eyebrow"><span className="status-dot" /> YOUR DAY, WITH ROOM TO MOVE</p>
          <h2 id="hero-title">Life happens<br />between<br /><em>appointments.</em></h2>
          <p className="hero-description">Your calendar knows where you need to be. Glide makes room for getting there.</p>
          <p className="hero-detail">Thoughtful travel time, added to your Google Calendar. So your day feels a little less back-to-back.</p>
          <div className="hero-actions">
            <button type="button" className="primary" data-tour="start-sample" onClick={onStart} disabled={busy}>
              {busy ? "Creating sample…" : "Try a sample day"}<span aria-hidden="true">↗</span>
            </button>
            <span className="small muted">No account needed. Just a little curiosity.</span>
          </div>
          <ConnectionStatus onDisconnected={onDisconnected} />
          {error && <p className="error" role="alert">{error}</p>}
        </div>
        <div className="day-preview" aria-label="Illustration of a day with travel time. Fictional example.">
          <div className="preview-top"><span className="eyebrow">A DAY THAT FLOWS</span><span className="preview-spark" aria-hidden="true">✳</span></div>
          <div className="preview-date"><span>Your day</span><span className="small">A little more breathing room</span></div>
          <div className="preview-event"><span className="preview-time">09:00</span><div><strong>Coffee & a catch-up</strong><span>The corner café</span></div><span className="event-dot" /></div>
          <div className="preview-journey"><span className="route-line" aria-hidden="true" /><div><span className="small">↗ &nbsp; Travel with Glide</span><strong>Time to take the scenic route.</strong><span>20 min drive + 10 min to settle in</span></div><span className="journey-check" aria-hidden="true">✓</span></div>
          <div className="preview-event"><span className="preview-time">10:30</span><div><strong>The next big idea</strong><span>Studio on the square</span></div><span className="event-dot clay" /></div>
          <div className="preview-space"><span aria-hidden="true">↓</span><span>Room for the journey.</span></div>
          <div className="preview-event"><span className="preview-time">12:00</span><div><strong>Lunch with a friend</strong><span>Somewhere lovely</span></div><span className="event-dot gold" /></div>
          <div className="preview-footer"><span className="status-dot" /> A fictional day. Real peace of mind.</div>
          <span className="preview-caption">LESS RUSH. MORE LIFE.</span>
        </div>
      </section>
      <section id="how-it-works" className="how-it-works" aria-labelledby="how-title">
        <div className="how-intro"><p className="eyebrow">A THOUGHTFUL +1</p><h2 id="how-title">A calendar that<br />connects the dots.</h2></div>
        <article><span className="step-number">01 /</span><h3>Bring your plans.</h3><p>Connect Google Calendar, or explore a fictional sample day with simulated routes.</p></article>
        <article><span className="step-number">02 /</span><h3>Make space to travel.</h3><p>Glide adds clearly marked driving time and an arrival buffer between appointments.</p></article>
        <article><span className="step-number">03 /</span><h3>Keep the final say.</h3><p>When the timing doesn’t fit, you decide. Your ordinary appointments stay yours.</p></article>
      </section>
      <footer className="site-footer"><span>Made for the space between.</span><VersionBadge /><span>Glide only manages its own travel events.</span></footer>
    </main>
  );
}
