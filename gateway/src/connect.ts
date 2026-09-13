import type { ConnectRouter } from "@connectrpc/connect";
import { QueryService } from "./gen/hydra/v1/query_pb.js";
import { supervisorClient } from "./supervisor-client.js";

// The gateway is intentionally "thin": it terminates the public protocols
// (Connect / gRPC / gRPC-Web) for browser + service clients, then forwards
// the call straight through to the Supervisor over internal pure gRPC and
// re-streams whatever comes back. Auth/RBAC/rate-limiting middleware slots
// in here as interceptors in a later phase — this is the seam for it.
export default (router: ConnectRouter) => {
  router.service(QueryService, {
    async *submitQuery(req, context) {
      for await (const chunk of supervisorClient.submitQuery(req, {
        signal: context.signal, // client disconnects -> upstream call is cancelled too
      })) {
        yield chunk;
      }
    },

    async health() {
      try {
        return await supervisorClient.health({});
      } catch {
        return {
          status: "degraded",
          dependencies: { supervisor: false },
        };
      }
    },
  });
};
