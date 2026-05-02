const crypto = require('crypto');

const RELAY_URL = 'wss://physical-mover-control-production.up.railway.app/operator';
const MOWER_RELAY_URL = 'wss://physical-mover-control-production.up.railway.app/mower';
const REPO_URL = 'https://github.com/fr1-ai/physical-mover-control.git';

function timingSafeEqualStr(a, b) {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

module.exports = async (req, res) => {
  if (req.method === 'OPTIONS') {
    res.status(204).end();
    return;
  }
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method_not_allowed' });
    return;
  }

  const expectedUser = process.env.OPERATOR_USERNAME || '';
  const expectedPass = process.env.OPERATOR_PASSWORD || '';
  const token = process.env.OPERATOR_TOKEN || '';
  if (!expectedUser || !expectedPass || !token) {
    res.status(500).json({ error: 'server_not_configured' });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  const username = (body && body.username) || '';
  const password = (body && body.password) || '';

  const userOk = timingSafeEqualStr(username, expectedUser);
  const passOk = timingSafeEqualStr(password, expectedPass);
  if (!userOk || !passOk) {
    await new Promise(r => setTimeout(r, 250));
    res.status(401).json({ error: 'invalid_credentials' });
    return;
  }

  const mowerToken = process.env.MOWER_TOKEN || '';
  const piSetup = mowerToken ? {
    clone: `git clone ${REPO_URL} ~/physical-mover-control`,
    bootstrap: `cd ~/physical-mover-control && RELAY_URL=${MOWER_RELAY_URL} MOWER_TOKEN=${mowerToken} bash scripts/bootstrap.sh`,
    logs: 'journalctl -u mower-client -f'
  } : null;

  res.status(200).json({ token, relayUrl: RELAY_URL, piSetup });
};
