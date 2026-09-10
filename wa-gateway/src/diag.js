// Metadata-only diagnostic helpers for the inbound pipeline.
//
// Privacy contract: log routing metadata (ids, JID kinds, message types,
// counts, statuses, timings). NEVER log message content, phone numbers in
// full, credentials, auth state, QR data, or service keys.
function jidKind(jid) {
  const j = String(jid || '');
  if (j.endsWith('@g.us')) return 'group';
  if (j === 'status@broadcast' || j.includes('@broadcast')) return 'broadcast';
  if (j.includes('@newsletter')) return 'newsletter';
  if (j.includes('@lid')) return 'lid-dm';
  if (j.includes('@s.whatsapp.net')) return 'dm';
  return 'other';
}

function last4(value) {
  const digits = String(value || '').replace(/\D/g, '');
  return digits.length >= 4 ? `***${digits.slice(-4)}` : '***';
}

function contentKeys(message) {
  if (!message || typeof message !== 'object') return [];
  return Object.keys(message).slice(0, 8);
}

module.exports = { jidKind, last4, contentKeys };
