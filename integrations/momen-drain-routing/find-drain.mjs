const endpoint = process.env.DRAIN_WEBHOOK_URL;
if (!endpoint) {
  console.error('Set DRAIN_WEBHOOK_URL to the Momen webhook URL (kept out of the repo on purpose).');
  process.exit(2);
}
const raw = process.argv.slice(2);
if (raw.length === 1 && raw[0] === '--help') {
  console.log('Usage: node find-drain.mjs <latitude> <longitude> <distance_meters>');
} else if (raw.length !== 3 || raw.some(value => value.trim() === '' || !Number.isFinite(Number(value)))) {
  console.error('Usage: node find-drain.mjs <latitude> <longitude> <distance_meters>');
  process.exitCode = 2;
} else {
  const [latitude, longitude, max_walking_distance_meters] = raw.map(Number);
  try {
    const response = await fetch(endpoint, {
      method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ latitude, longitude, max_walking_distance_meters }),
      signal: AbortSignal.timeout(35000)
    });
    if (!response.ok) {
      const requestId = response.headers.get('x-request-id');
      throw new Error(`Request failed: HTTP ${response.status}${requestId ? ` (request ID: ${requestId})` : ''}.`);
    }
    const result = await response.json();
    if (!['FOUND', 'NO_MATCH', 'INVALID_INPUT'].includes(result?.status)) {
      throw new Error('Unexpected response from the routing backend.');
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
