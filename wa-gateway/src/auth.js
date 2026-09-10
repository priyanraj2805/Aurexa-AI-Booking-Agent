// Shared-secret gate for backend<->gateway calls. Health stays public.
// Reads the key lazily so tests can set process.env per case.
function serviceKey() {
  return process.env.WA_GATEWAY_SERVICE_KEY || '';
}

function requireServiceKey(req, res, next) {
  const got = req.header('X-Gateway-Key') || '';
  const want = serviceKey();
  if (!want || got !== want) {
    return res.status(401).json({ detail: 'Invalid gateway credentials' });
  }
  return next();
}

module.exports = { requireServiceKey, serviceKey };
