
/* ══════════════════════ 圖示 ══════════════════════ */
const P = {
  plus:'<path d="M12 5v14M5 12h14"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  more:'<circle cx="12" cy="5" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="19" r="1.3"/>',
  sparkle:'<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
  chevDown:'<path d="M6 9l6 6 6-6"/>',
  chevRight:'<path d="M9 6l6 6-6 6"/>',
  copy:'<rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6a2 2 0 012-2h9"/>',
  refresh:'<path d="M20 11a8 8 0 10-2.3 6.1"/><path d="M20 5v6h-6"/>',
  retry:'<path d="M4 11a8 8 0 0113.7-5.6L20 8"/><path d="M20 4v4h-4"/>',
  clip:'<path d="M20 11.5l-8 8a5 5 0 01-7-7l8.5-8.5a3.4 3.4 0 014.8 4.8L9.9 17.2a1.8 1.8 0 01-2.5-2.5l7.8-7.8"/>',
  send:'<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop:'<rect x="7" y="7" width="10" height="10" rx="2"/>',
  moon:'<path d="M20.5 14.3A8.5 8.5 0 019.7 3.5a8.5 8.5 0 1010.8 10.8z"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
  sliders:'<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="13" cy="18" r="2"/>',
  check:'<path d="M20 6L9 17l-5-5"/>',
  alert:'<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5v.4"/>',
  trash:'<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12"/><path d="M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2"/>',
  node:'<path d="M12 3l7.5 4.5v9L12 21l-7.5-4.5v-9z"/><circle cx="12" cy="12" r="2.6"/>',
  pencil:'<path d="M4 20h4l10.5-10.5a2.8 2.8 0 10-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  wrench:'<path d="M15.5 3.5a5 5 0 00-6.2 6.2L3.6 15.4a2 2 0 102.8 2.8l5.7-5.7a5 5 0 006.2-6.2l-2.9 2.9-2.3-.6-.6-2.3z"/>',
  branch:'<circle cx="7" cy="5" r="2.2"/><circle cx="7" cy="19" r="2.2"/><circle cx="17" cy="9" r="2.2"/><path d="M7 7.2v9.6M17 11.2c0 3.3-3.3 3.3-6.6 3.8"/>',
  box:'<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  compare:'<rect x="3" y="4" width="7.5" height="16" rx="2"/><rect x="13.5" y="4" width="7.5" height="16" rx="2"/>',
  edit:'<path d="M4 20h4l10-10a2.8 2.8 0 10-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  gear:'<circle cx="12" cy="12" r="3.2"/><path d="M19.4 14a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-1.8-.3 1.6 1.6 0 00-1 1.5V20a2 2 0 11-4 0v-.1A1.6 1.6 0 008 18.4a1.6 1.6 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.6 1.6 0 00.3-1.8 1.6 1.6 0 00-1.5-1H2a2 2 0 110-4h.1A1.6 1.6 0 003.6 8a1.6 1.6 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.6 1.6 0 001.8.3H8a1.6 1.6 0 001-1.5V2a2 2 0 114 0v.1a1.6 1.6 0 001 1.5 1.6 1.6 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8V8a1.6 1.6 0 001.5 1H22a2 2 0 110 4h-.1a1.6 1.6 0 00-1.5 1z"/>',
  compress:'<path d="M4 9h5V4M20 15h-5v5"/><path d="M9 9L3.5 3.5M15 15l5.5 5.5"/>',
  panelL:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  panelR:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>'
};
function ico(name, size, w) {
  return '<svg width="' + (size || 16) + '" height="' + (size || 16) + '" viewBox="0 0 24 24" ' +
    'fill="none" stroke="currentColor" stroke-width="' + (w || 1.9) + '" ' +
    'stroke-linecap="round" stroke-linejoin="round">' + P[name] + '</svg>';
}

