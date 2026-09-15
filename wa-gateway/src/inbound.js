// Inbound Baileys message normalization (V1: 1:1 customer DMs only).
//
// Returns an envelope { messageId, from, chatJid, text, timestamp, pushName }
// or null with a skip reason. Groups, status, broadcasts, newsletters, own
// messages, protocol/control messages and non-text content are ignored here
// so they can never enter the appointment-booking flow.
function digitsOf(value) {
  return String(value || '').replace(/\D/g, '');
}

function extractText(message) {
  if (!message || typeof message !== 'object') return null;
  if (typeof message.conversation === 'string' && message.conversation.trim()) {
    return message.conversation.trim();
  }
  const ext = message.extendedTextMessage;
  if (ext && typeof ext.text === 'string' && ext.text.trim()) return ext.text.trim();
  const btn = message.buttonsResponseMessage;
  if (btn) {
    const t = btn.selectedDisplayText || btn.selectedButtonId;
    if (t && String(t).trim()) return String(t).trim();
  }
  const list = message.listResponseMessage;
  if (list) {
    // Baileys list tap: singleSelectReply holds the stable rowId
    const sel = list.singleSelectReply;
    if (sel && typeof sel.selectedRowId === 'string' && sel.selectedRowId.trim()) return sel.selectedRowId.trim();
    if (list.title && String(list.title).trim()) return String(list.title).trim();
  }
  return null;
}

function extractOptionId(message) {
  if (!message || typeof message !== 'object') return null;
  const btn = message.buttonsResponseMessage;
  if (btn && typeof btn.selectedButtonId === 'string' && btn.selectedButtonId.trim()) return btn.selectedButtonId.trim();
  const list = message.listResponseMessage;
  if (list && list.singleSelectReply && typeof list.singleSelectReply.selectedRowId === 'string' && list.singleSelectReply.selectedRowId.trim()) return list.singleSelectReply.selectedRowId.trim();
  return null;
}

function normalizeInbound(msg) {
  if (!msg || typeof msg !== 'object' || !msg.key) return { skip: 'no-key' };
  const key = msg.key;
  if (key.fromMe) return { skip: 'from-me' };
  const remoteJid = String(key.remoteJid || '');
  if (!remoteJid) return { skip: 'no-remote-jid' };
  if (remoteJid.includes('@g.us')) return { skip: 'group' };
  if (remoteJid === 'status@broadcast' || remoteJid.includes('@broadcast')) return { skip: 'broadcast' };
  if (remoteJid.includes('@newsletter')) return { skip: 'newsletter' };
  if (msg.message && (msg.message.protocolMessage || msg.message.reactionMessage)) {
    return { skip: 'protocol' };
  }
  if (!key.id) return { skip: 'no-message-id' };
  // Prefer the phone-number JID when Baileys exposes a linked identity.
  const alt = msg.remoteJidAlt ? String(msg.remoteJidAlt).split('@')[0] : '';
  const from = digitsOf(alt) || digitsOf(remoteJid.split('@')[0]);
  if (from.length < 7) return { skip: 'bad-sender' };
  const text = extractText(msg.message);
  if (!text) return { skip: 'no-text' };
  const optionId = extractOptionId(msg.message);
  return {
    envelope: {
      messageId: String(key.id),
      from,
      chatJid: remoteJid,
      text,
      optionId: optionId || null,
      timestamp: Number(msg.messageTimestamp) || Math.floor(Date.now() / 1000),
      pushName: typeof msg.pushName === 'string' ? msg.pushName.slice(0, 128) : null,
    },
  };
}

module.exports = { normalizeInbound, extractText, digitsOf };
