-- Add dev_port to projects for auto-registering Tailscale Serve on launch.
-- Each project can have a fixed dev server port (e.g., 3000, 5173, 8787).
-- launch_project.py auto-runs `tailscale serve --bg <port>` when launching.

ALTER TABLE projects ADD COLUMN dev_port INTEGER;
