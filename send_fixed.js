// Outbound Baileys renderer — patched to force numbered fallback for now
// because interactive list/buttons are being downgraded to extendedTextMessage
// by Baileys/WhatsApp for this business account. Numbered text is always
// deliverable and matches the directive's stable IDs via number → option.

function toBaileysPayload(reply) {
  const type = reply && reply.type;
  const body = String((reply && reply.body) || '');
  if (type === 'interactive_buttons' && Array.isArray(reply.buttons) && reply.buttons.length > 0) {
    const buttons = reply.buttons.slice(0, 3).map((b, i) => ({
      buttonId: String(b.id || `opt_${i}`),
      buttonText: { displayText: String(b.title || `Option ${i + 1}`).slice(0, 20) },
      type: 1,
    }));
    return {
      kind: 'buttons',
      payload: { text: body, buttons, headerType: 1 },
      fallbackText: numberedFallback(body, reply.buttons.map((b) => b.title)),
    };
  }
  if (type === 'interactive_list' && Array.isArray(reply.sections) && reply.sections.length > 0) {
    const sections = reply.sections.slice(0, 10).map((s, si) => ({
      title: String(s.title || `Options ${si + 1}`).slice(0, 24),
      rows: (Array.isArray(s.rows) ? s.rows : []).slice(0, 10).map((r, ri) => ({
        title: String(r.title || `Option ${ri + 1}`).slice(0, 24),
        rowId: String(r.id || `opt_${si}_${ri}`),
        description: r.description ? String(r.description).slice(0, 72) : undefined,
      })),
    }));
    const flat = [];
    reply.sections.forEach((s) => (Array.isArray(s.rows) ? s.rows : []).forEach((r) => flat.push(r.title)));
    return {
      kind: 'list',
      payload: {
        text: body,
        buttonText: String(reply.button_text || 'Choose').slice(0, 20),
        sections,
      },
      fallbackText: numberedFallback(body, flat),
    };
  }
  return { kind: 'text', payload: { text: body }, fallbackText: body };
}

function numberedFallback(body, options) {
  const lines = (options || []).map((o, i) => `${i + 1}. ${o}`);
  return lines.length > 0 ? `${body}\n\n${lines.join('\n')}\n\nReply with the option number.` : body;
}

async function sendViaHandle(handle, chatJid, reply) {
  const rendered = toBaileysPayload(reply);
  if (typeof handle.presence === 'function') {
    try { await handle.presence(chatJid, 'composing'); } catch (_) {}
  }
  // PATCH: Force numbered fallback for interactive to guarantee delivery
  // Keep rich attempt but log; always fallback to text if we are interactive
  // because current Baileys is downgrading to extendedTextMessage without numbers
  let useFallbackForced = rendered.kind !== 'text';
  try {
    if (useFallbackForced) {
      // Send fallback directly — user sees 1. Mon, Sep 14 etc. and can reply 1-7
      // Still try rich first in background for future when WhatsApp allows it
      try { await handle.sendRaw(chatJid, rendered.payload); } catch (_) {}
      return await handle.sendRaw(chatJid, { text: rendered.fallbackText });
    }
    return await handle.sendRaw(chatJid, rendered.payload);
  } catch (err) {
    if (rendered.kind === 'text') throw err;
    return handle.sendRaw(chatJid, { text: rendered.fallbackText });
  } finally {
    if (typeof handle.presence === 'function') {
      try { await handle.presence(chatJid, 'paused'); } catch (_) {}
    }
  }
}

module.exports = { toBaileysPayload, numberedFallback, sendViaHandle };
