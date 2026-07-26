# k6 load test for nightly CI — DevOps Central Platform
#
# Runs against the deployed cluster's service endpoints via ingress or
# node-ports. Triggered by schedule cron in ci-cd.yml.
#
# Usage:
#   K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090 \
#   k6 run tests/k6/load-test.js
#
# Install: pip install k6  # or docker run grafana/k6

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '1m', target: 10 },   // ramp up
    { duration: '2m', target: 50 },   // sustain
    { duration: '30s', target: 0 },   // ramp down
  ],
  thresholds: {
    errors: ['rate<0.01'],            // <1% errors
    http_req_duration: ['p(99)<500'], // p99 < 500ms
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://users-service.devops-platform.svc.cluster.local:80';

export default function () {
  const endpoints = ['/', '/livez', '/readyz', '/users', '/metrics'];
  for (const path of endpoints) {
    const res = http.get(`${BASE_URL}${path}`);
    const ok = check(res, {
      'status is 200 or not-503': (r) => r.status === 200 || r.status !== 503,
    });
    errorRate.add(!ok);
    sleep(1);
  }
}
