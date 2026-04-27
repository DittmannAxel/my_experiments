FROM nodered/node-red:latest

USER root

# Pre-install OPC UA contrib node + Dashboard 2.0
RUN cd /usr/src/node-red \
 && npm install --omit=dev --unsafe-perm --no-audit --no-update-notifier --no-fund \
        node-red-contrib-opcua \
        @flowfuse/node-red-dashboard

USER node-red

# Settings + flows are baked in (so the dashboard is ready on first boot).
COPY --chown=node-red:node-red settings.js /data/settings.js
COPY --chown=node-red:node-red flows.json /data/flows.json

ENV NODE_RED_ENABLE_PROJECTS=false
EXPOSE 1880

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
  CMD wget -qO /dev/null http://localhost:1880/nodered/ || exit 1
