import * as http2 from "node:http2";
import { connectNodeAdapter } from "@connectrpc/connect-node";
import routes from "./connect.js";
import { config } from "./config.js";

export function startServer() {
  // h2c (HTTP/2 cleartext) is fine for local dev / behind an internal LB
  // that terminates TLS. Swap to http2.createSecureServer(...) in prod.
  const server = http2.createServer(
    connectNodeAdapter({
      routes,
      // All three protocols are enabled by default: Connect, gRPC, gRPC-Web.
      // Browsers use Connect (plain fetch), other backends can use gRPC.
    })
  );

  server.listen(config.port, () => {
    console.log(
      `[gateway] listening on :${config.port} (connect+grpc+grpc-web) -> supervisor@${config.supervisorUrl}`
    );
  });

  return server;
}
