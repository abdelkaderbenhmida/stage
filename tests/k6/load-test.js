import http from "k6/http";
import { check, sleep } from "k6";

// Nightly drift/regression load test for users-service (ci-cd.yml load-test
// job, schedule-only). BASE_URL defaults to the in-cluster ClusterIP DNS
// name; override via the K6_BASE_URL repo variable for other environments.
const BASE_URL = __ENV.BASE_URL || "http://users-service.devops-platform.svc.cluster.local:80";

export const options = {
  vus: 5,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
  },
};

export default function () {
  const health = http.get(`${BASE_URL}/livez`);
  check(health, { "livez is 200": (r) => r.status === 200 });

  const users = http.get(`${BASE_URL}/users`);
  check(users, { "users is 200": (r) => r.status === 200 });

  sleep(1);
}
