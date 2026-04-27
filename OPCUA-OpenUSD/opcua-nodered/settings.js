/* Node-RED settings for the Robot Twin PoC.
 * Mounted into the container as /data/settings.js.
 *
 * The Traefik prefix is /nodered, so we set httpAdminRoot accordingly.
 * Node-RED handles the prefix itself (BUILD.md Trap 6: do NOT stripPrefix
 * in Traefik).
 */
module.exports = {
  uiPort: process.env.PORT || 1880,
  flowFile: "flows.json",
  // Persist credentials encryption with a fixed PoC key. For PoC only.
  credentialSecret: process.env.NODE_RED_CRED_SECRET || "axel-robot-twin",
  flowFilePretty: true,

  // Path prefix — must match Traefik route below.
  httpAdminRoot: "/nodered",
  httpNodeRoot:  "/nodered",

  // Dashboard 2.0 mounts at httpNodeRoot + ui-base.path. We set ui-base.path
  // to "/dashboard" in flows.json so the dashboard lives at /nodered/dashboard/.
  ui: { path: "/nodered/ui" },

  // Disable projects (we use plain flows.json on disk).
  editorTheme: {
    projects: { enabled: false },
    page: {
      title: "Axel Robot Twin — Node-RED",
    },
  },

  logging: {
    console: {
      level: "info",
      metrics: false,
      audit: false,
    },
  },

  exportGlobalContextKeys: false,

  // Allow function nodes to import a small set of modules.
  functionGlobalContext: {},
  functionExternalModules: false,
};
