// Rank active, in-range drains by urgency, then by straight-line distance.
function numeric(value) {
  return typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')
    ? Number(value) : NaN;
}

function pointOf(location) {
  // Actionflow bindings can serialize a GeoJSON point as text.
  if (typeof location === 'string') {
    try { location = JSON.parse(location); } catch (_) { return null; }
  }
  const coordinates = Array.isArray(location) ? location
    : location && location.type === 'Point' ? location.coordinates : null;
  if (!Array.isArray(coordinates) || coordinates.length !== 2) return null;
  const lng = numeric(coordinates[0]);
  const lat = numeric(coordinates[1]);
  return Number.isFinite(lat) && Math.abs(lat) <= 90 && Number.isFinite(lng) && Math.abs(lng) <= 180
    ? [lng, lat] : null;
}

function straightLineMeters(lat, lng, destLat, destLng) {
  const rad = Math.PI / 180;
  const a = Math.sin((destLat - lat) * rad / 2) ** 2
    + Math.cos(lat * rad) * Math.cos(destLat * rad) * Math.sin((destLng - lng) * rad / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(Math.max(0, Math.min(1, a))));
}

function chooseDrain(latitude, longitude, radius, drains) {
  if (!Number.isFinite(latitude) || Math.abs(latitude) > 90 ||
      !Number.isFinite(longitude) || Math.abs(longitude) > 180 ||
      !Number.isFinite(radius) || radius < 0) {
    return { status: 'INVALID_INPUT', message: 'Provide a valid latitude, longitude, and nonnegative distance in meters.' };
  }
  let best = null;
  for (const row of Array.isArray(drains) ? drains : []) {
    if (!row || row.is_active !== true || row.id == null || typeof row.name !== 'string') continue;
    const point = pointOf(row.location);
    const urgency = numeric(row.urgency_score);
    if (!point || !Number.isFinite(urgency)) continue;
    const [lng, lat] = point;
    const distance = straightLineMeters(latitude, longitude, lat, lng);
    if (distance > radius) continue;
    if (!best || urgency > best.urgency || (urgency === best.urgency && distance < best.distance)) {
      best = { row, lat, lng, urgency, distance };
    }
  }
  if (!best) return { status: 'NO_MATCH', message: 'No active drain was found within the requested straight-line distance limit.' };
  const origin = encodeURIComponent(latitude + ',' + longitude);
  const destination = encodeURIComponent(best.lat + ',' + best.lng);
  return {
    status: 'FOUND',
    message: 'Highest-urgency active drain within the straight-line limit. Google Maps provides walking directions; actual walking distance may exceed the limit.',
    drain_id: best.row.id,
    drain_name: best.row.name,
    address: best.row.address == null ? '' : best.row.address,
    urgency_score: best.urgency,
    urgency_reason: best.row.urgency_reason == null ? '' : best.row.urgency_reason,
    straight_line_distance_meters: best.distance,
    directions_url: 'https://www.google.com/maps/dir/?api=1&origin=' + origin + '&destination=' + destination + '&travelmode=walking'
  };
}

context.setResult(chooseDrain(
  numeric(context.getArg('latitude')),
  numeric(context.getArg('longitude')),
  numeric(context.getArg('max_walking_distance_meters')),
  context.getArg('drains')
));
