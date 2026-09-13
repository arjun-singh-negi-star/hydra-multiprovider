export const config = {
  /** Port the gateway itself listens on (Connect + gRPC + gRPC-Web, multiplexed). */
  port: Number(process.env.GATEWAY_PORT ?? 8080),

  /** Where the Python Supervisor's pure-gRPC server is reachable. */
  supervisorUrl: process.env.SUPERVISOR_URL ?? "http://localhost:50051",

  /** Fail fast in dev if someone forgets to set this in prod. */
  nodeEnv: process.env.NODE_ENV ?? "development",
};
