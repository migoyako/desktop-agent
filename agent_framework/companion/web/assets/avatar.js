/* Shared desk-pet artwork: one renderer for the header slot and the overlay.
   M13.3 replaces this module with the Live2D stage, so this file is the only
   place that draws the programmatic figure. */
(function (global) {
  "use strict";

  // Mirrors AffectService labels (affect.py): warm/positive share the smiling
  // face, low/tired/concerned the flat one, neutral/calm the resting face.
  const WARM = ["warm", "positive"];
  const LOW = ["low", "tired", "concerned"];

  function group(mood) {
    const label = String(mood || "");
    if (WARM.includes(label)) return "warm";
    if (LOW.includes(label)) return "low";
    return "calm";
  }

  function svg(size, mood, className) {
    const kind = group(mood);
    const eyeY = kind === "low" ? 55 : 51;
    const mouth =
      kind === "warm"
        ? '<path d="M64 64 q11 12 22 0" fill="none" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>'
        : kind === "low"
        ? '<path d="M66 69 h18" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>'
        : '<path d="M65 64 q10 8 20 0" fill="none" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>';
    const cls = className ? ' class="' + className + '"' : "";
    return `<svg${cls} width="${size}" height="${size}" viewBox="0 0 150 150" aria-hidden="true">
      <ellipse cx="75" cy="126" rx="44" ry="10" fill="rgba(0,0,0,.24)"/>
      <line x1="75" y1="20" x2="75" y2="10" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <circle cx="75" cy="7" r="6" fill="var(--accent)"/>
      <rect x="39" y="58" width="72" height="62" rx="25" fill="var(--avatar-body)" stroke="var(--avatar-outline)" stroke-width="2"/>
      <ellipse cx="75" cy="52" rx="38" ry="34" fill="var(--avatar-body)" stroke="var(--avatar-outline)" stroke-width="2"/>
      <circle cx="61" cy="${eyeY}" r="4.5" fill="var(--avatar-ink)"/>
      <circle cx="89" cy="${eyeY}" r="4.5" fill="var(--avatar-ink)"/>
      ${mouth}
      <line x1="42" y1="76" x2="30" y2="96" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <line x1="108" y1="76" x2="120" y2="96" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <line x1="58" y1="118" x2="54" y2="130" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <line x1="92" y1="118" x2="96" y2="130" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
    </svg>`;
  }

  function mount(node, size, mood, className) {
    if (node) node.innerHTML = svg(size, mood, className);
  }

  global.CompanionAvatar = { group: group, mount: mount, svg: svg };
})(window);
