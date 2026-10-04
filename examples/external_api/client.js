// Node.js 18+: read an exported credential file; never follow redirects with secrets.
const fs = require("node:fs");

async function main() {
  const [file, name] = process.argv.slice(2);
  if (!file || !name) throw new Error("Usage: node client.js <hpc-api.json> <api-name>");
  const config = JSON.parse(fs.readFileSync(file, "utf8"));
  if (config.version !== 1 || !Object.prototype.hasOwnProperty.call(config.apis || {}, name))
    throw new Error("API is missing from the exported configuration");
  const base = new URL(config.apis[name]);
  const portal = new URL(config.base_url);
  if (base.protocol !== "https:" || base.origin !== portal.origin ||
      base.username || base.password || base.search || base.hash ||
      !base.pathname.startsWith("/hub/user-api/" + encodeURIComponent(config.username) + "/") || !base.pathname.endsWith("/"))
    throw new Error("Invalid API URL");
  for (const key of ["client_id", "client_secret", "jupyterhub_token"])
    if (typeof config[key] !== "string" || !config[key]) throw new Error("Missing credentials");
  const response = await fetch(new URL("api/analyze", base), {
    method: "POST", redirect: "error", signal: AbortSignal.timeout(60000),
    headers: {
      "CF-Access-Client-ID": config.client_id,
      "CF-Access-Client-Secret": config.client_secret,
      Authorization: "token " + config.jupyterhub_token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({values: [1, 2, 3]}),
  });
  if (!response.ok) throw new Error("API request failed (HTTP " + response.status + ")");
  console.log(await response.json());
}
main().catch((error) => { console.error(error.message); process.exitCode = 1; });
