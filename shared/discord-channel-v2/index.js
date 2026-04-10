/**
 * SoY Discord Channel v2 — MCP Channel server for Claude Code.
 *
 * Bridges Discord to a persistent Claude Code session via the native
 * channels feature. Claude maintains full conversation context in memory —
 * no history replay needed, massive token savings.
 *
 * Architecture:
 *   Claude Code spawns this as a subprocess via --channels
 *   This server connects to Discord via discord.js
 *   Incoming Discord messages → MCP channel notifications → Claude
 *   Claude replies → MCP tool calls → Discord messages
 *
 * Fallback: If this crashes, the v1 Python bot (discord_bot.py) takes over.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { Client, GatewayIntentBits, EmbedBuilder } from "discord.js";
import { readFileSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

// ── Config ──

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = process.env.CLAUDE_PLUGIN_ROOT || join(__dirname, "../..");
const ENV_PATH = join(PLUGIN_ROOT, ".env");

// Load .env
const env = {};
if (existsSync(ENV_PATH)) {
  for (const line of readFileSync(ENV_PATH, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    let [k, ...rest] = trimmed.split("=");
    let v = rest.join("=").trim();
    if (v.length >= 2 && v[0] === v[v.length - 1] && (v[0] === '"' || v[0] === "'"))
      v = v.slice(1, -1);
    env[k.trim()] = v;
  }
}

const DISCORD_TOKEN = env.DISCORD_BOT_TOKEN || process.env.DISCORD_BOT_TOKEN || "";
const OWNER_ID = env.DISCORD_OWNER_ID || process.env.DISCORD_OWNER_ID || "";
const NUDGES_CHANNEL = env.DISCORD_NUDGES_CHANNEL || process.env.DISCORD_NUDGES_CHANNEL || "nudges";
const DB_PATH = join(PLUGIN_ROOT, "data", "soy.db");

// ── Logging (to stderr — stdout is MCP protocol) ──

function log(...args) {
  process.stderr.write(`[soy-discord] ${args.join(" ")}\n`);
}

// ── Database (via sqlite3 CLI — avoids native module compilation) ──

function sqliteQuery(sql) {
  try {
    const result = execSync(`sqlite3 -json "${DB_PATH}" "${sql.replace(/"/g, '\\"')}"`, {
      encoding: "utf8",
      timeout: 5000,
    });
    return result.trim() ? JSON.parse(result) : [];
  } catch (e) {
    log("SQLite error:", e.message);
    return [];
  }
}

function sqliteExec(sql) {
  try {
    execSync(`sqlite3 "${DB_PATH}" "${sql.replace(/"/g, '\\"')}"`, {
      encoding: "utf8",
      timeout: 5000,
    });
    return true;
  } catch (e) {
    log("SQLite exec error:", e.message);
    return false;
  }
}

// ── SQL Helpers ──

function esc(s) {
  return (s || "").replace(/'/g, "''");
}

// ── SoY Context Builder ──

function buildSoyContext() {
  try {
    const ownerRows = sqliteQuery(
      "SELECT value FROM user_profile WHERE category = 'identity' AND key = 'name'"
    );
    const ownerName = ownerRows[0]?.value || "there";

    const projects = sqliteQuery(
      `SELECT p.name, p.status, c.name as client,
       (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done') as open_tasks,
       (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done') as done_tasks
       FROM projects p LEFT JOIN contacts c ON c.id = p.client_id
       WHERE p.status IN ('active', 'planning') ORDER BY p.name`
    );

    const tasks = sqliteQuery(
      `SELECT t.title, t.priority, p.name as project_name
       FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
       WHERE t.status != 'done'
       ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
       WHEN 'medium' THEN 2 ELSE 3 END LIMIT 15`
    );

    const projectNames = projects.map((p) => p.name);

    let ctx = `Owner: ${ownerName}\n\n`;
    ctx += "## Projects\n";
    for (const p of projects) {
      ctx += `- ${p.name} (${p.status}) — ${p.open_tasks} open, ${p.done_tasks} done`;
      if (p.client) ctx += `, client: ${p.client}`;
      ctx += "\n";
    }
    ctx += "\n## Priority Tasks\n";
    for (const t of tasks) {
      ctx += `- [${t.priority || "medium"}] ${t.title}`;
      if (t.project_name) ctx += ` (${t.project_name})`;
      ctx += "\n";
    }
    ctx += `\n## Known Project Names\n${JSON.stringify(projectNames)}\n`;

    return ctx;
  } catch (e) {
    log("Warning: Could not load SoY context:", e.message);
    return "";
  }
}

function getChannelProject(channelId) {
  try {
    const rows = sqliteQuery(
      `SELECT dcp.project_id, dcp.project_name, p.workspace_path
       FROM discord_channel_projects dcp
       JOIN projects p ON p.id = dcp.project_id
       WHERE dcp.channel_id = '${channelId}'`
    );
    return rows[0] || null;
  } catch {
    return null;
  }
}

// ── Task/Note Capture ──

function captureMarkers(text) {
  const captured = [];

  // Tasks
  for (const match of text.matchAll(/\[TASK:\s*([^\]]+)\]/g)) {
    const parts = match[1].split("|").map((s) => s.trim());
    const title = parts[0];
    const projectName = parts[1] || null;
    const priority = parts[2] || "medium";
    if (!title) continue;

    let projectId = "NULL";
    if (projectName) {
      const rows = sqliteQuery(
        `SELECT id FROM projects WHERE LOWER(name) LIKE LOWER('%${esc(projectName)}%')`
      );
      if (rows[0]) projectId = rows[0].id;
    }

    const validPriorities = ["low", "medium", "high", "urgent"];
    const pri = validPriorities.includes(priority) ? priority : "medium";

    sqliteExec(
      `INSERT INTO tasks (project_id, title, status, priority, created_at, updated_at)
       VALUES (${projectId}, '${esc(title)}', 'todo', '${pri}', datetime('now'), datetime('now'))`
    );

    captured.push(`Task: ${title}`);
  }

  // Notes
  for (const match of text.matchAll(/\[NOTE:\s*([^\]]+)\]/g)) {
    const parts = match[1].split("|").map((s) => s.trim());
    const title = parts[0];
    const content = parts[1] || "";
    const projectName = parts[2] || "";
    if (!title) continue;

    sqliteExec(
      `INSERT INTO standalone_notes (title, content, linked_projects, created_at, updated_at)
       VALUES ('${esc(title)}', '${esc(content)}', '${esc(projectName)}', datetime('now'), datetime('now'))`
    );

    captured.push(`Note: ${title}`);
  }

  // Clean markers from text
  let cleaned = text
    .replace(/\[TASK:\s*[^\]]+\]\s*\n?/g, "")
    .replace(/\[NOTE:\s*[^\]]+\]\s*\n?/g, "")
    .replace(/\[EXPENSE:\s*[^\]]+\]\s*\n?/g, "")
    .replace(/\[HANDOFF_PICKED_UP\]\s*\n?/g, "")
    .trim();

  return { cleaned, captured };
}

// ── Discord Client ──

const discord = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

let mcp = null;
let discordReady = false;
const processStartTime = Date.now();

// ── MCP Channel Server ──

const soyContext = buildSoyContext();

const instructions = `You are the Discord interface for Software of You (SoY) — a personal data platform.
You're running as a persistent Claude Code session with full conversation memory.
Messages arrive as <channel source="soy_discord" ...> tags.

## SoY Data
${soyContext}

## Behavior
- Reply using the "reply" tool, passing back the chat_id from the channel tag.
- For rich formatted messages (embeds with fields, colors), use the "reply_embed" tool.
- Keep responses concise. Use Discord markdown (**bold**, *italic*, \`code\`, etc.).
- When the user mentions a task, include [TASK: title | project_name | priority] in your reply.
- When the user shares a note/idea, include [NOTE: title | content | project_name].
- When the message has a project_name attribute, default to that project for context.
- Never fabricate data. If you don't know, say so.
- For code changes, guide users to /dev slash commands.
- You have access to the full codebase when workspace_path is provided.`;

async function startMcp() {
  mcp = new Server(
    { name: "soy-discord", version: "2.0.0" },
    {
      capabilities: {
        experimental: { "claude/channel": {} },
        tools: {},
      },
      instructions,
    }
  );

  // ── Tools ──

  mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "reply",
        description:
          "Send a text message back to the Discord channel/thread. Pass chat_id from the <channel> tag.",
        inputSchema: {
          type: "object",
          properties: {
            chat_id: {
              type: "string",
              description: "Discord channel or thread ID from the channel tag",
            },
            text: { type: "string", description: "Message text (Discord markdown supported)" },
          },
          required: ["chat_id", "text"],
        },
      },
      {
        name: "reply_embed",
        description:
          "Send a rich embed message to Discord. Use for structured data like status, tasks, sessions.",
        inputSchema: {
          type: "object",
          properties: {
            chat_id: { type: "string", description: "Discord channel or thread ID" },
            title: { type: "string", description: "Embed title" },
            description: { type: "string", description: "Embed body text" },
            color: {
              type: "number",
              description: "Embed color as decimal (e.g. 5865F2 for blurple = 5793266)",
            },
            fields: {
              type: "array",
              description: "Embed fields",
              items: {
                type: "object",
                properties: {
                  name: { type: "string" },
                  value: { type: "string" },
                  inline: { type: "boolean" },
                },
                required: ["name", "value"],
              },
            },
          },
          required: ["chat_id", "title"],
        },
      },
      {
        name: "get_project_context",
        description:
          "Load current SoY project data (tasks, contacts, recent activity) for a specific project.",
        inputSchema: {
          type: "object",
          properties: {
            project_name: { type: "string", description: "Project name to look up" },
          },
          required: ["project_name"],
        },
      },
    ],
  }));

  mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
    const { name, arguments: args } = req.params;

    if (name === "reply") {
      const { chat_id, text } = args;
      try {
        const channel = await discord.channels.fetch(chat_id);
        if (!channel) throw new Error(`Channel ${chat_id} not found`);

        // Parse markers before sending
        const { cleaned, captured } = captureMarkers(text);
        let finalText = cleaned;
        if (captured.length > 0) {
          finalText += "\n\n*Captured:*\n" + captured.map((c) => `- ${c}`).join("\n");
        }

        // Split long messages
        const chunks = chunkText(finalText, 2000);
        for (const chunk of chunks) {
          await channel.send(chunk);
        }

        return { content: [{ type: "text", text: "sent" }] };
      } catch (e) {
        log("Reply error:", e.message);
        return { content: [{ type: "text", text: `error: ${e.message}` }] };
      }
    }

    if (name === "reply_embed") {
      const { chat_id, title, description, color, fields } = args;
      try {
        const channel = await discord.channels.fetch(chat_id);
        if (!channel) throw new Error(`Channel ${chat_id} not found`);

        const embed = new EmbedBuilder().setTitle(title);
        if (description) embed.setDescription(description);
        if (color) embed.setColor(color);
        if (fields) {
          for (const f of fields) {
            embed.addFields({ name: f.name, value: f.value, inline: f.inline || false });
          }
        }

        await channel.send({ embeds: [embed] });
        return { content: [{ type: "text", text: "embed sent" }] };
      } catch (e) {
        log("Embed error:", e.message);
        return { content: [{ type: "text", text: `error: ${e.message}` }] };
      }
    }

    if (name === "get_project_context") {
      const { project_name } = args;
      try {
        const projects = sqliteQuery(
          `SELECT * FROM projects WHERE LOWER(name) LIKE LOWER('%${esc(project_name)}%')`
        );
        const project = projects[0];

        if (!project) {
          return {
            content: [{ type: "text", text: `Project "${project_name}" not found.` }],
          };
        }

        const tasks = sqliteQuery(
          `SELECT title, priority, status, due_date FROM tasks WHERE project_id = ${project.id}
           ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END`
        );

        let ctx = `## ${project.name} (${project.status})\n`;
        if (project.description) ctx += `${project.description}\n`;
        if (project.workspace_path) ctx += `Workspace: ${project.workspace_path}\n`;
        ctx += `\n### Tasks (${tasks.length})\n`;
        for (const t of tasks) {
          ctx += `- [${t.priority || "medium"}] ${t.title} (${t.status})`;
          if (t.due_date) ctx += ` — due ${t.due_date}`;
          ctx += "\n";
        }

        return { content: [{ type: "text", text: ctx }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Error: ${e.message}` }] };
      }
    }

    throw new Error(`Unknown tool: ${name}`);
  });

  // Connect MCP over stdio
  const transport = new StdioServerTransport();
  await mcp.connect(transport);
  log("MCP channel server connected");
}

// ── Discord Event Handlers ──

discord.on("ready", () => {
  discordReady = true;
  log(`Discord connected as ${discord.user.tag}`);
});

discord.on("messageCreate", async (message) => {
  // Ignore own messages
  if (message.author.id === discord.user.id) return;

  // Owner gate
  if (message.author.id !== OWNER_ID) return;

  // Don't process if MCP isn't connected
  if (!mcp) return;

  // Handle !reset command — restart the v2 service to clear context
  if (message.content.trim().toLowerCase() === "!reset") {
    log("!reset command received — restarting v2 service to clear context");
    try {
      await message.reply("\u267b\ufe0f Resetting context... restarting v2 session. Back in ~30s.");
      // Give Discord a moment to send the reply, then restart
      setTimeout(() => {
        execSync("systemctl --user restart soy-discord-v2.service", {
          timeout: 10000,
        });
      }, 1500);
    } catch (e) {
      log("Reset error:", e.message);
      try { await message.reply("\u26a0\ufe0f Reset failed: " + e.message); } catch {}
    }
    return;
  }

  // Handle !status command — show context age
  if (message.content.trim().toLowerCase() === "!status") {
    const uptimeMs = Date.now() - processStartTime;
    const hours = Math.floor(uptimeMs / 3600000);
    const mins = Math.floor((uptimeMs % 3600000) / 60000);
    try {
      await message.reply(
        `ℹ️ **Session Status**\nUptime: ${hours}h ${mins}m\nContext accumulates over session lifetime. Use \`!reset\` to clear.`
      );
    } catch {}
    return;
  }

  const channel = message.channel;
  const channelId = channel.id;
  const isThread = channel.isThread();
  const parentChannelId = isThread ? channel.parentId : channelId;

  // Build message content with attachment descriptions
  let content = message.content || "";
  const attachments = [];
  for (const att of message.attachments.values()) {
    const ext = att.name?.split(".").pop()?.toLowerCase() || "";
    if (["jpg", "jpeg", "png", "webp", "gif"].includes(ext)) {
      attachments.push(`[Image: ${att.name}]`);
    } else if (ext === "pdf") {
      attachments.push(`[PDF: ${att.name}]`);
    } else {
      attachments.push(`[File: ${att.name}]`);
    }
  }

  if (attachments.length > 0) {
    content = attachments.join(" ") + (content ? "\n" + content : "");
  }

  if (!content.trim()) return;

  // Resolve project from channel
  const project = getChannelProject(parentChannelId);
  const projectName = project?.project_name || "";
  const workspacePath = project?.workspace_path || "";

  // Build metadata
  const meta = {
    chat_id: channelId,
    user_id: message.author.id,
    message_id: message.id,
    channel_name: channel.name || "DM",
    is_thread: isThread ? "true" : "false",
  };

  if (isThread) {
    meta.parent_channel_id = parentChannelId;
    meta.thread_name = channel.name || "";
  }

  if (projectName) {
    meta.project_name = projectName;
  }
  if (workspacePath) {
    meta.workspace_path = workspacePath;
  }

  log(`Message from #${meta.channel_name}: ${content.slice(0, 80)}`);

  // Push to Claude via MCP channel notification
  try {
    await mcp.notification({
      method: "notifications/claude/channel",
      params: { content, meta },
    });
  } catch (e) {
    log("MCP notification error:", e.message);
  }
});

// ── Helpers ──

function chunkText(text, maxLen = 2000) {
  if (text.length <= maxLen) return [text];
  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= maxLen) {
      chunks.push(remaining);
      break;
    }
    let idx = remaining.lastIndexOf("\n\n", maxLen);
    if (idx < maxLen / 2) idx = remaining.lastIndexOf("\n", maxLen);
    if (idx < maxLen / 2) idx = maxLen;
    chunks.push(remaining.slice(0, idx));
    remaining = remaining.slice(idx).trimStart();
  }
  return chunks;
}

// ── Startup ──

async function main() {
  if (!DISCORD_TOKEN) {
    log("Error: DISCORD_BOT_TOKEN not set");
    process.exit(1);
  }
  if (!OWNER_ID) {
    log("Error: DISCORD_OWNER_ID not set");
    process.exit(1);
  }

  // Start MCP server first (Claude Code is waiting for stdio handshake)
  await startMcp();

  // Then connect to Discord
  await discord.login(DISCORD_TOKEN);

  log("SoY Discord Channel v2 running");
}

main().catch((e) => {
  log("Fatal:", e.message);
  process.exit(1);
});
