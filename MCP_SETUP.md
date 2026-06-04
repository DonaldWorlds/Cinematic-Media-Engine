# MCP Setup for Cinematic Media Engine

Current as of May 2026. The `claude mcp add` command syntax changed recently — these commands are verified.

---

## Important: The 40-Tool Ceiling

Claude Code has a soft ceiling of ~40 active tools across all MCP servers combined. Exceed it and Claude Code silently loses access to some tools. Install only what you need.

**For this project, install 5 MCPs. Skip the rest.**

---

## The Stack for This Project

These five MCPs cover everything you need without hitting the ceiling:

1. **Filesystem** — read/write your project files
2. **Memory** — persistent context across sessions
3. **Context7** — current library docs at request time
4. **GitHub** — repo, issues, PRs
5. **Sequential Thinking** — better reasoning on hard problems (scoring, tuning)

That's it. Don't add more until you find a gap the existing five can't cover.

---

## Install Commands

Run these in your terminal. Each one registers the MCP at the user level so it's available in all your Claude Code projects.

### 1. Filesystem MCP

Lets Claude Code read/write files in directories you specify. Replace `/Users/yourname` with your actual home directory or specific project path.

```bash
claude mcp add --transport stdio filesystem -- \
  npx -y @modelcontextprotocol/server-filesystem \
  /Users/yourname/code \
  /Users/yourname/Documents
```

You can list multiple directories. Add the path containing your projects and any other folder Claude Code needs access to (fixture videos, brand assets, etc.).

### 2. Memory MCP

Persistent memory across Claude Code sessions. Claude Code remembers decisions, context, and ongoing project state.

```bash
claude mcp add --transport stdio memory -- \
  npx -y @modelcontextprotocol/server-memory
```

No config needed. It just works.

### 3. Context7 MCP

Pulls current docs for any library at request time. Eliminates stale library knowledge — when Claude Code needs the faster-whisper or TwelveLabs API, it reads the real current docs instead of guessing from training data.

```bash
claude mcp add --transport stdio context7 -- \
  npx -y @upstash/context7-mcp
```

### 4. GitHub MCP

Read your repos, issues, PRs, commits. Requires a GitHub personal access token.

**First, create a token:**
1. Go to https://github.com/settings/tokens
2. Generate new token (classic)
3. Scopes: `repo`, `read:org`, `read:user`
4. Copy the token

**Then install with the token:**
```bash
claude mcp add --transport stdio github \
  --env GITHUB_PERSONAL_ACCESS_TOKEN=<paste-token-here> \
  -- npx -y @modelcontextprotocol/server-github
```

### 5. Sequential Thinking MCP

Forces step-by-step reasoning on complex problems. Useful when tuning scoring weights, debugging pipeline issues, or designing algorithm logic.

```bash
claude mcp add --transport stdio sequential-thinking -- \
  npx -y @modelcontextprotocol/server-sequential-thinking
```

---

## Verification

After installing, verify everything works:

```bash
# List all installed MCPs
claude mcp list

# Get details on a specific MCP
claude mcp get filesystem
claude mcp get memory
claude mcp get context7
claude mcp get github
claude mcp get sequential-thinking
```

Each should show as connected with tools registered. If any shows zero tools, the server started but failed to register — usually an env var or path issue.

---

## In a Claude Code Session

Once installed, the MCPs auto-load when you start Claude Code. To check what's active in a session:

```
/mcp
```

This lists active MCP servers and their tools.

---

## Troubleshooting

### "Command not found: claude"
Install Claude Code first:
```bash
# macOS via Homebrew (recommended for M1)
brew install --cask claude-code

# Or via the official installer
curl -fsSL https://claude.ai/install.sh | bash
```

### "npx not found"
Install Node.js. On M1 Mac:
```bash
brew install node
```

### MCP installs but shows zero tools
The server started but failed to register. Run the install command manually in terminal and read stderr. Usually a missing env var or wrong package name.

### Filesystem MCP can't see a directory
The path must be passed at install time. To add more directories later, remove and re-add:
```bash
claude mcp remove filesystem
claude mcp add --transport stdio filesystem -- \
  npx -y @modelcontextprotocol/server-filesystem \
  /path/one /path/two /path/three
```

### GitHub MCP authentication fails
Token scope issue. Regenerate with `repo`, `read:org`, `read:user` scopes minimum. Remove and re-add the MCP with the new token.

---

## What I'm Deliberately NOT Installing

These are useful in some workflows but not for this project. Adding them eats into the 40-tool ceiling without payoff.

- **Brave Search / Tavily** — Claude Code already has web search built in. Redundant.
- **Slack / Linear / Notion** — you don't use these for this project.
- **Playwright / Puppeteer** — no browser automation needed for the engine itself.
- **Postgres / SQLite MCP** — your project doesn't have a database yet.
- **Sentry** — not in production yet, no error monitoring needed.

Add these only if and when you have an actual use case.

---

## Order to Run Them

Tomorrow morning, before opening Claude Code:

1. Verify Claude Code is installed: `claude --version`
2. Install all 5 MCPs (commands above)
3. Verify: `claude mcp list`
4. Open Claude Code in your project directory
5. Type `/mcp` to confirm they're active
6. Tell Claude Code: "Read SPEC.md, CLAUDE.md, and KNOWLEDGE.md. Then we begin Phase 1."

You're set up.

---

## Optional: Skill for Global Preferences

Beyond MCPs, create a Skill at `~/.claude/skills/kkthedon-stack/SKILL.md` with your global preferences (hardware, tools, your name, your projects). This auto-loads in every Claude Code session, project-independent.

If you want me to write that file too, ask.
