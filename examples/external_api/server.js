// No portal authentication code is needed: the platform owns the authenticated relay.
const http = require("node:http");
const port = Number(process.env.PORT || 3000);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid PORT");
const host = "127.0.0.1";
http.createServer((req, res) => {
  function reply(status, body) {
    res.writeHead(status, {"Content-Type": "application/json"});
    res.end(JSON.stringify(body));
  }
  if (req.method === "GET" && req.url === "/health") return reply(200, {status: "ok"});
  if (req.method !== "POST" || req.url !== "/api/analyze") return reply(404, {error: "not found"});
  let body = "", oversized = false;
  req.on("data", (chunk) => {
    if (oversized) return;
    body += chunk.toString();
    if (Buffer.byteLength(body) > 1024 * 1024) { oversized = true; body = ""; reply(413, {error: "too large"}); }
  });
  req.on("end", () => {
    if (oversized) return;
    try {
      const data = JSON.parse(body);
      if (!Array.isArray(data.values) || !data.values.every(Number.isFinite)) throw new Error();
      reply(200, {sum: data.values.reduce((sum, value) => sum + value, 0)});
    } catch (_) { reply(400, {error: "values must be an array of finite numbers"}); }
  });
}).listen(port, host);
