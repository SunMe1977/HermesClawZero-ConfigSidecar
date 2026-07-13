// k6 load test — HermesClawZero 2M-scale verification
// Run: k6 run loadtest.js
// Install: brew install k6  (macOS)  or  winget install k6  (Windows)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://localhost:8010';
const KEY = __ENV.API_KEY || 'ci-test-key-123';

const failRate = new Rate('failed_requests');
const healthLatency = new Trend('health_latency');
const searchLatency = new Trend('search_latency');

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // ramp up to 10 users
    { duration: '1m',  target: 50 },   // ramp to 50
    { duration: '2m',  target: 100 },  // ramp to 100
    { duration: '1m',  target: 200 },  // peak at 200
    { duration: '30s', target: 0 },    // ramp down
  ],
  thresholds: {
    failed_requests: ['rate<0.05'],           // <5% failure rate
    http_req_duration: ['p(95)<2000'],         // 95% under 2s
    health_latency: ['p(95)<500'],             // health check fast
  },
};

export default function () {
  const params = { headers: { 'Content-Type': 'application/json' } };

  // 1. Health check (always light)
  {
    const t0 = Date.now();
    const r = http.get(`${BASE}/healthz`);
    healthLatency.add(Date.now() - t0);
    const ok = check(r, { 'healthz status 200': (res) => res.status === 200 });
    failRate.add(!ok);
  }

  // 2. Search (simulates user search)
  {
    const queries = ['docker', 'memory', 'python', 'ollama', 'config', 'api', 'test', 'backup', 'pgvector', 'setup'];
    const q = queries[Math.floor(Math.random() * queries.length)];
    const t0 = Date.now();
    const r = http.get(`${BASE}/search?query=${q}&key=${KEY}&limit=5`);
    searchLatency.add(Date.now() - t0);
    const ok = check(r, { 'search status 200': (res) => res.status === 200 });
    failRate.add(!ok);
  }

  // 3. Capture (10% of requests - write path)
  if (Math.random() < 0.1) {
    const text = `Load test memory — ${Math.random().toString(36).substring(2, 10)}`;
    const r = http.post(`${BASE}/capture?key=${KEY}`,
      JSON.stringify({ text }),
      params
    );
    const ok = check(r, { 'capture status 200/201': (res) => res.status === 200 || res.status === 201 });
    failRate.add(!ok);
  }

  // 4. Version endpoint (read path, no auth needed)
  {
    const r = http.get(`${BASE}/version`);
    const ok = check(r, { 'version status 200': (res) => res.status === 200 });
    failRate.add(!ok);
  }

  sleep(0.5 + Math.random()); // 0.5-1.5s between requests
}
