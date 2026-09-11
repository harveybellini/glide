export default function Brand() {
  return (
    <span className="brand">
      <svg className="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
        <rect width="40" height="40" rx="13" fill="currentColor" />
        <path d="M10 24c8 0 7-12 20-12M10 29c12 0 10-12 20-12" stroke="#f8f7ef" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <span>glide<span className="brand-dot">.</span></span>
    </span>
  );
}
