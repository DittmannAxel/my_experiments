FROM nginx:1.27-alpine
COPY index.html /usr/share/nginx/html/index.html
COPY health.html /usr/share/nginx/html/health.html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://localhost/health.html || exit 1
