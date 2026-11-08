// Starts the interactive API docs at /api/docs. Kept in its own file because the
// site's Content-Security-Policy runs no inline scripts. Swagger UI 5.34.1 (Apache-2.0)
// is served from this folder instead of a CDN for the same reason.
window.ui = SwaggerUIBundle({
  url: "/api/openapi.json",
  dom_id: "#swagger-ui",
  deepLinking: true,
  layout: "BaseLayout",
});
