import { createClient } from "@connectrpc/connect";
import { createGrpcTransport } from "@connectrpc/connect-node";
import { QueryService } from "./gen/hydra/v1/query_pb.js";
import { config } from "./config.js";

// This is the "Gateway <-> Supervisor : pure gRPC" leg of the architecture.
// Same generated QueryService, different transport than the one the public
// Connect server below uses — that's the whole point of Connect being
// protocol-agnostic on the client side too.
const supervisorTransport = createGrpcTransport({
  baseUrl: config.supervisorUrl,
});

export const supervisorClient = createClient(QueryService, supervisorTransport);
