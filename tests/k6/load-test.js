import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '1m', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://users-service.devops-platform.svc.cluster.local:80';

export default function () {
  const live = http.get(`${BASE_URL}/livez`);
  check(live, { 'livez 200': (r) => r.status === 200 });

  const data = http.get(`${BASE_URL}/users`);
  check(data, { 'users 200': (r) => r.status === 200 });

  sleep(1);
}
