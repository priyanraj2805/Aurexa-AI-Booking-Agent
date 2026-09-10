// P8 tests: Baileys reply rendering + outbox-routed sends.
const assert = require('node:assert');
const test = require('node:test');

const { toBaileysPayload, numberedFallback, sendViaHandle } = require('../src/send');
const { createSessionManager } = require('../src/sessionManager');
const { createOutbox } = require('../src/outbox');

test('text reply maps to plain text payload', () => {
  const out = toBaileysPayload({ type: 'text', body: 'Hello!' });
  assert.strictEqual(out.kind, 'text');
  assert.deepStrictEqual(out.payload, { text: 'Hello!' });
});

test('buttons map to Baileys buttons with numbered fallback', () => {
  const out = toBaileysPayload({
    type: 'interactive_buttons',
    body: 'Confirm?',
    buttons: [
      { id: 'btn_confirm', title: 'Confirm' },
      { id: 'btn_edit', title: 'Edit' },
      { id: 'btn_cancel', title: 'Cancel' },
      { id: 'btn_extra', title: 'Extra' },
    ],
  });
  assert.strictEqual(out.kind, 'buttons');
  assert.strictEqual(out.payload.buttons.length, 3); // WhatsApp cap
  assert.strictEqual(out.payload.buttons[0].buttonId, 'btn_confirm');
  assert.ok(out.fallbackText.includes('1. Confirm'));
  assert.ok(out.fallbackText.includes('Reply with the option number.'));
});

test('lists map to Baileys list payload with fallback', () => {
  const out = toBaileysPayload({
    type: 'interactive_list',
    body: 'Pick a time',
    button_text: 'Choose',
    sections: [{ title: 'Morning', rows: [{ id: 'a', title: '10:00' }, { id: 'b', title: '11:00' }] }],
  });
  assert.strictEqual(out.kind, 'list');
  assert.strictEqual(out.payload.buttonText, 'Choose');
  assert.strictEqual(out.payload.sections[0].rows[1].rowId, 'b');
  assert.ok(out.fallbackText.includes('2. 11:00'));
});

test('unknown directive falls back to text', () => {
  const out = toBaileysPayload({ type: 'SOME_FUTURE_CARD', body: 'Hi' });
  assert.strictEqual(out.kind, 'text');
});

test('sendViaHandle sends rich first, falls back to text, always pauses typing', async () => {
  const calls = [];
  const handle = {
    async sendRaw(chatJid, payload) {
      calls.push({ chatJid, payload });
      if (payload.buttons) throw new Error('buttons deprecated here');
      return { key: { id: 'SENT' } };
    },
    async presence(chatJid, state) {
      calls.push({ presence: state });
    },
  };
  const res = await sendViaHandle(handle, '123@s.whatsapp.net', {
    type: 'interactive_buttons',
    body: 'Confirm?',
    buttons: [{ id: 'yes', title: 'Yes' }],
  });
  assert.strictEqual(res.key.id, 'SENT');
  const kinds = calls.map((c) => (c.presence ? `presence:${c.presence}` : 'send'));
  assert.deepStrictEqual(kinds, ['presence:composing', 'send', 'send', 'presence:paused']);
  assert.ok(calls[2].payload.text.includes('1. Yes'));
});

test('sendViaHandle typing failure never fails the send', async () => {
  const handle = {
    async sendRaw(chatJid, payload) {
      assert.strictEqual(payload.text, 'Hi');
      return { ok: true };
    },
    async presence() {
      throw new Error('presence unsupported');
    },
  };
  const res = await sendViaHandle(handle, '123@s.whatsapp.net', { type: 'text', body: 'Hi' });
  assert.deepStrictEqual(res, { ok: true });
});

const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';

test('inbound reply routes through the outbox (paced, deduped)', async () => {
  const sent = [];
  const sleepLog = [];
  const outbox = createOutbox({
    minGapMs: 5,
    maxPerMinute: 1000,
    delay: async (ms) => { sleepLog.push(ms); },
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  const mgr = createSessionManager({
    makeSocket: async () => ({
      async logout() {},
      async end() {},
      async sendRaw(chatJid, payload) {
        sent.push({ chatJid, payload });
        return { key: { id: 'S1' } };
      },
      async presence() {},
    }),
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-send-test',
    outbox,
    deliverInbound: async () => ({ reply: { type: 'text', body: 'Ack' } }),
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  await mgr.start(ID_A);
  const msg = (id) => ({
    key: { remoteJid: DM, fromMe: false, id },
    message: { conversation: 'Hi' },
    messageTimestamp: 1700000000,
  });
  const r1 = await mgr.handleInboundMessage(ID_A, msg('M1'));
  assert.deepStrictEqual(r1, { delivered: true, replied: true });
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0].payload.text, 'Ack');
  // Same inbound message id re-delivered (gateway retry): reply suppressed.
  const r2 = await mgr.handleInboundMessage(ID_A, msg('M1'));
  assert.deepStrictEqual(r2, { delivered: true, replied: true });
  assert.strictEqual(sent.length, 1);
});
